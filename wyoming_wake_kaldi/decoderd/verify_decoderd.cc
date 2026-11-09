// Resident grammar decoder for wake verification / alignment.
//
// Loads the acoustic model, i-vector extractor, and ALL of the verifier's
// decoding graphs once, then decodes wav files on demand: one wav path per
// stdin line, one result line per graph on stdout.
//
// The point of handling every graph in one process is that the acoustic model
// is then evaluated ONCE per utterance instead of once per graph. The four
// verifier graphs score the same audio against different grammars, so the
// nnet3 forward pass -- about 95 ms of a 1.4 s window on a Pi 4 -- was being
// done four times over identical input. Here a single feature pipeline feeds a
// single decodable, and the graph searches share it.
//
//   verify_decoderd [options] <final.mdl> <tag> <HCLG.fst> <word_boundary.int|-> ...
//
// with the last three arguments repeated once per graph.
//
// Protocol (line oriented):
//   stdin:  <wav-path>[<TAB><tag>,<tag>...]
//   stdout: exactly one line per graph decoded -- every graph, or those
//           tagged after a tab, in the order the graphs were given:
//             OK <tag> <total_best_path_cost> <n_output_frames> [wordid:beg:end ...]
//             ERR <tag> <message>
//   "READY" is printed once after loading. Word times are in OUTPUT frames
//   (multiply by 0.01 * frame-subsampling-factor for seconds); word ids map
//   through the graph's words.txt. Word alignment entries are only produced
//   for graphs given a word_boundary.int file.
//
// Streams: audio decoded as it comes, so that little is left to do when it
// ends (a call, while it is said). Each has an id the client picks; several
// may be open at once, their audio interleaved:
//   OPEN <id>[<TAB><tag>,<tag>...]     no output
//   AUDIO <id> <n>                     then n bytes of 16 kHz mono s16le; no output
//   CLOSE <id>[<TAB><tag>,<tag>...]    the result lines, as for a wav path
//                                      (the tags again: an unknown stream
//                                      still answers one ERR line per graph)
//   DROP <id>                          forgotten; no output
// Only a wav path and CLOSE answer, so a client that writes those and reads
// their lines in turn never reads another request's. The audio is fed in the
// same chunks as a wav file's (--chunk-length), whatever sizes it comes in:
// a stream decodes as the same audio from a file would.
//
// Two things make the sharing exact rather than approximate:
//
//   * The decoders are advanced ONE output frame at a time, in lockstep. The
//     looped decodable computes log-posteriors in chunks and refuses frames
//     requested out of order ("Frames must be accessed in order"), so letting
//     any one decoder run ahead would leave the others asking for frames the
//     cache has already passed.
//
//   * Each graph keeps its OWN decoder config. They share the acoustic model,
//     not the search: --max-active must not apply to the confusable graph,
//     whose whole job is to find a rival hypothesis that capping prunes away.
//     --uncapped-graphs names the exceptions. (Getting this wrong is not
//     subtle in its effect but is silent in its symptoms: it halves the
//     confusable margin and accepts false positives.)
//
//   * i-vector silence weighting needs a decoder traceback, and there is now
//     one feature stream rather than four, so --silence-weighting-graph picks
//     which graph drives it (default: the first). With the shipped model this
//     turns out not to matter -- the config carries no silence weighting, so
//     the choice is inert -- but it is there if a model enables it.
//
// Costs/alignments are otherwise equivalent to the subprocess pipeline
// (online2-wav-nnet3-latgen-faster | lattice-1best | lattice-align-words |
// lattice-best-path / nbest-to-ctm) with the same options.

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <sstream>
#include <string>
#include <vector>

#include "feat/wave-reader.h"
#include "fstext/kaldi-fst-io.h"
#include "lat/determinize-lattice-pruned.h"
#include "lat/lattice-functions.h"
#include "lat/word-align-lattice.h"
#include "nnet3/decodable-online-looped.h"
#include "nnet3/nnet-utils.h"
#include "online2/online-endpoint.h"
#include "online2/online-nnet2-feature-pipeline.h"
#include "online2/online-nnet3-decoding.h"

namespace {

using namespace kaldi;

struct Graph {
  std::string tag;
  fst::Fst<fst::StdArc> *fst = nullptr;
  WordBoundaryInfo *word_boundary = nullptr;
  // Per-graph, because max_active must differ: see --uncapped-graphs.
  LatticeFasterDecoderConfig opts;
};

// What every decode shares: the model, its configs, the graphs.
struct Shared {
  const TransitionModel *trans_model = nullptr;
  OnlineNnet2FeaturePipelineInfo *feature_info = nullptr;
  nnet3::DecodableNnetSimpleLoopedInfo *decodable_info = nullptr;
  int32 frame_subsampling_factor = 1;
  std::vector<Graph> *graphs = nullptr;
  int32 silence_weighting_index = 0;
  BaseFloat chunk_length_secs = 0.18;
};

// One decode in progress: a wav file's, or a stream's as its audio comes.
struct Stream {
  std::vector<int32> active;  // graph indices, sorted
  int32 weighting = 0;        // the graph driving i-vector silence weighting
  BaseFloat samp_freq = 16000;
  std::unique_ptr<OnlineNnet2FeaturePipeline> features;
  std::unique_ptr<nnet3::DecodableAmNnetLoopedOnline> decodable;
  std::vector<std::unique_ptr<LatticeFasterOnlineDecoder>> decoders;  // by graph
  std::unique_ptr<OnlineSilenceWeighting> silence_weighting;
  std::vector<std::pair<int32, BaseFloat>> delta_weights;
  std::vector<BaseFloat> pending;  // less than a chunk, not yet fed
  std::string error;               // the first failure: reported at the end
  uint64 opened = 0;               // for dropping the oldest of too many
};

// Split a comma-separated option value.
std::vector<std::string> SplitCommas(const std::string &value) {
  std::vector<std::string> out;
  std::string item;
  std::istringstream stream(value);
  while (std::getline(stream, item, ',')) {
    if (!item.empty()) {
      out.push_back(item);
    }
  }
  return out;
}

// Total graph+acoustic cost and frame count of a linear best path, matching
// what lattice-best-path reports.
void BestPathCost(const CompactLattice &best_path, double *total_cost,
                  int64 *n_frames) {
  *total_cost = 0.0;
  *n_frames = 0;
  typedef CompactLatticeArc Arc;
  Arc::StateId state = best_path.Start();
  while (state != fst::kNoStateId) {
    const auto final_weight = best_path.Final(state);
    if (final_weight != CompactLatticeWeight::Zero()) {
      *total_cost +=
          final_weight.Weight().Value1() + final_weight.Weight().Value2();
      *n_frames += final_weight.String().size();
      break;
    }
    fst::ArcIterator<CompactLattice> aiter(best_path, state);
    if (aiter.Done()) {
      break;
    }
    const Arc &arc = aiter.Value();
    *total_cost += arc.weight.Weight().Value1() + arc.weight.Weight().Value2();
    *n_frames += arc.weight.String().size();
    state = arc.nextstate;
  }
}

// The graphs a request names (comma-separated tags), sorted; every one when
// it names none.
std::vector<int32> ActiveGraphs(const std::vector<Graph> &graphs,
                                const std::string &tags) {
  std::vector<int32> active;
  for (const std::string &tag : SplitCommas(tags)) {
    for (int32 g = 0; g < static_cast<int32>(graphs.size()); g++) {
      if (graphs[g].tag == tag &&
          std::find(active.begin(), active.end(), g) == active.end()) {
        active.push_back(g);
      }
    }
  }
  std::sort(active.begin(), active.end());
  if (active.empty()) {
    for (int32 g = 0; g < static_cast<int32>(graphs.size()); g++) {
      active.push_back(g);
    }
  }
  return active;
}

void Open(const Shared &shared, Stream *stream) {
  std::vector<Graph> &graphs = *shared.graphs;
  // The traceback silence weighting reads: its graph, if decoded.
  stream->weighting =
      std::find(stream->active.begin(), stream->active.end(),
                shared.silence_weighting_index) != stream->active.end()
          ? shared.silence_weighting_index
          : stream->active[0];
  stream->features.reset(new OnlineNnet2FeaturePipeline(*shared.feature_info));
  // ONE decodable: the acoustic model runs once for all the graphs.
  stream->decodable.reset(new nnet3::DecodableAmNnetLoopedOnline(
      *shared.trans_model, *shared.decodable_info,
      stream->features->InputFeature(), stream->features->IvectorFeature()));
  stream->decoders.resize(graphs.size());
  for (int32 g : stream->active) {
    // (fst, config): borrows the FST. The (config, FST*) overload takes
    // ownership, which would delete a graph we reuse every turn.
    stream->decoders[g].reset(
        new LatticeFasterOnlineDecoder(*graphs[g].fst, graphs[g].opts));
    stream->decoders[g]->InitDecoding();
  }
  stream->silence_weighting.reset(new OnlineSilenceWeighting(
      *shared.trans_model, shared.feature_info->silence_weighting_config,
      shared.frame_subsampling_factor));
}

// One chunk into the features (``finished``: the last), and every decoder
// advanced over what that made ready.
void Advance(Stream *stream, const BaseFloat *samples, int32 n,
             bool finished) {
  OnlineNnet2FeaturePipeline &features = *stream->features;
  if (n > 0) {
    Vector<BaseFloat> chunk(n, kUndefined);
    std::copy(samples, samples + n, chunk.Data());
    features.AcceptWaveform(stream->samp_freq, chunk);
  }
  if (finished) {
    features.InputFinished();
  }
  if (stream->silence_weighting->Active() &&
      features.IvectorFeature() != nullptr) {
    stream->silence_weighting->ComputeCurrentTraceback(
        *stream->decoders[stream->weighting]);
    stream->silence_weighting->GetDeltaWeights(features.NumFramesReady(),
                                               &stream->delta_weights);
    features.IvectorFeature()->UpdateFrameWeights(stream->delta_weights);
  }
  // Lockstep, one output frame at a time: the looped decodable caches a chunk
  // of log-posteriors and rejects frames requested before its current
  // offset, so no decoder may run ahead of the others.
  LatticeFasterOnlineDecoder &first = *stream->decoders[stream->active[0]];
  while (first.NumFramesDecoded() < stream->decodable->NumFramesReady()) {
    for (int32 g : stream->active) {
      stream->decoders[g]->AdvanceDecoding(stream->decodable.get(), 1);
    }
  }
}

// Audio as it comes: fed a chunk at a time, as a wav file's would be.
void Feed(const Shared &shared, Stream *stream, const BaseFloat *samples,
          size_t n) {
  if (!stream->error.empty()) {
    return;
  }
  int32 chunk = static_cast<int32>(stream->samp_freq * shared.chunk_length_secs);
  std::vector<BaseFloat> &pending = stream->pending;
  pending.insert(pending.end(), samples, samples + n);
  if (chunk <= 0) {
    return;  // all of it at the end
  }
  size_t at = 0;
  try {
    while (pending.size() - at >= static_cast<size_t>(chunk)) {
      Advance(stream, pending.data() + at, chunk, false);
      at += chunk;
    }
  } catch (const std::exception &e) {
    stream->error = e.what();
  }
  pending.erase(pending.begin(), pending.begin() + at);
}

// The end of the audio, and each graph's result line.
std::vector<std::string> Finish(const Shared &shared, Stream *stream) {
  std::vector<Graph> &graphs = *shared.graphs;
  std::vector<std::string> lines(graphs.size());
  try {
    if (!stream->error.empty()) {
      throw std::runtime_error(stream->error);
    }
    Advance(stream, stream->pending.data(),
            static_cast<int32>(stream->pending.size()), true);
    stream->pending.clear();

    for (int32 g : stream->active) {
      Graph &graph = graphs[g];
      LatticeFasterOnlineDecoder &decoder = *stream->decoders[g];
      decoder.FinalizeDecoding();

      if (decoder.NumFramesDecoded() == 0) {
        lines[g] = "ERR " + graph.tag + " no frames decoded";
        continue;
      }

      Lattice raw_lat;
      decoder.GetRawLattice(&raw_lat, true /* end_of_utterance */);
      CompactLattice clat;
      DeterminizeLatticePhonePrunedWrapper(*shared.trans_model, &raw_lat,
                                           graph.opts.lattice_beam, &clat,
                                           graph.opts.det_opts);
      if (clat.NumStates() == 0) {
        lines[g] = "ERR " + graph.tag + " empty lattice";
        continue;
      }

      CompactLattice best_path;
      CompactLatticeShortestPath(clat, &best_path);
      if (best_path.NumStates() == 0) {
        lines[g] = "ERR " + graph.tag + " empty best path";
        continue;
      }

      double total_cost = 0.0;
      int64 n_frames = 0;
      BestPathCost(best_path, &total_cost, &n_frames);

      std::ostringstream out;
      out << "OK " << graph.tag << " " << total_cost << " " << n_frames;

      if (graph.word_boundary != nullptr) {
        CompactLattice aligned;
        if (WordAlignLattice(best_path, *shared.trans_model,
                             *graph.word_boundary, 0 /* max_states: no limit */,
                             &aligned)) {
          std::vector<int32> words, times, lengths;
          if (CompactLatticeToWordAlignment(aligned, &words, &times,
                                            &lengths)) {
            for (size_t i = 0; i < words.size(); i++) {
              if (words[i] == 0) {
                continue;  // <eps> / silence gap
              }
              out << " " << words[i] << ":" << times[i] << ":"
                  << (times[i] + lengths[i]);
            }
          }
        }
      }
      lines[g] = out.str();
    }
  } catch (const std::exception &e) {
    for (int32 g : stream->active) {
      if (lines[g].empty()) {
        lines[g] = "ERR " + graphs[g].tag + " " + e.what();
      }
    }
  }
  std::vector<std::string> out;
  for (int32 g : stream->active) {
    out.push_back(lines[g].empty() ? "ERR " + graphs[g].tag + " no result"
                                   : lines[g]);
  }
  return out;
}

// Always exactly one line per graph decoded, so the client can read a fixed
// number of lines without a terminator.
void Answer(const std::vector<std::string> &lines) {
  for (const std::string &line : lines) {
    std::cout << line << "\n";
  }
  std::cout << std::flush;
}

// "<id>[<TAB><tags>]" after a stream request's keyword.
void IdAndTags(const std::string &rest, std::string *id, std::string *tags) {
  const size_t tab = rest.find('\t');
  *id = rest.substr(0, tab);
  *tags = tab == std::string::npos ? "" : rest.substr(tab + 1);
}

const size_t kMostStreams = 16;  // more open: the oldest is dropped

}  // namespace

int main(int argc, char *argv[]) {
  using namespace kaldi;
  try {
    const char *usage =
        "Resident wake-verification decoder; see header comment.\n"
        "Usage: verify_decoderd [options] <nnet3-in> "
        "(<tag> <fst-in> <word-boundary-int|->)+\n";

    ParseOptions po(usage);
    OnlineNnet2FeaturePipelineConfig feature_opts;
    nnet3::NnetSimpleLoopedComputationOptions decodable_opts;
    LatticeFasterDecoderConfig decoder_opts;
    WordBoundaryInfoNewOpts word_boundary_opts;
    // unused, but online.conf carries --endpoint.* options and the shared
    // config file must parse
    OnlineEndpointConfig endpoint_opts;

    // Match online2-wav-nnet3-latgen-faster --online=true exactly: chunked
    // feeding + i-vector silence weighting. Skipping this shifts best-path
    // costs by several nats (measured), which is material against the
    // verifier's calibrated thresholds.
    BaseFloat chunk_length_secs = 0.18;
    std::string silence_weighting_graph;
    std::string uncapped_graphs;

    feature_opts.Register(&po);
    decodable_opts.Register(&po);
    decoder_opts.Register(&po);
    endpoint_opts.Register(&po);
    po.Register("chunk-length", &chunk_length_secs,
                "Length of audio chunks fed to the feature pipeline "
                "(matches the reference binary's default)");
    po.Register("silence-weighting-graph", &silence_weighting_graph,
                "Tag of the graph whose traceback drives i-vector silence "
                "weighting (default: the first graph given)");
    po.Register("uncapped-graphs", &uncapped_graphs,
                "Comma-separated graph tags that ignore --max-active. The "
                "confusable graph must be listed: capping the number of "
                "active states prunes the rival hypothesis it exists to find, "
                "which silently accepts false positives");
    po.Read(argc, argv);

    if (po.NumArgs() < 4 || (po.NumArgs() - 1) % 3 != 0) {
      po.PrintUsage();
      return 1;
    }

    const std::string nnet3_rxfilename = po.GetArg(1);
    const int32 num_graphs = (po.NumArgs() - 1) / 3;

    OnlineNnet2FeaturePipelineInfo feature_info(feature_opts);

    TransitionModel trans_model;
    nnet3::AmNnetSimple am_nnet;
    {
      bool binary;
      Input ki(nnet3_rxfilename, &binary);
      trans_model.Read(ki.Stream(), binary);
      am_nnet.Read(ki.Stream(), binary);
      SetBatchnormTestMode(true, &(am_nnet.GetNnet()));
      SetDropoutTestMode(true, &(am_nnet.GetNnet()));
      nnet3::CollapseModel(nnet3::CollapseModelConfig(), &(am_nnet.GetNnet()));
    }
    nnet3::DecodableNnetSimpleLoopedInfo decodable_info(decodable_opts,
                                                        &am_nnet);

    const std::vector<std::string> uncapped = SplitCommas(uncapped_graphs);

    std::vector<Graph> graphs(num_graphs);
    int32 silence_weighting_index = 0;
    for (int32 g = 0; g < num_graphs; g++) {
      graphs[g].tag = po.GetArg(2 + 3 * g);
      graphs[g].opts = decoder_opts;
      if (std::find(uncapped.begin(), uncapped.end(), graphs[g].tag) !=
          uncapped.end()) {
        graphs[g].opts.max_active = std::numeric_limits<int32>::max();
      }
      graphs[g].fst = fst::ReadFstKaldiGeneric(po.GetArg(3 + 3 * g));
      const std::string wb = po.GetArg(4 + 3 * g);
      if (wb != "-") {
        graphs[g].word_boundary =
            new WordBoundaryInfo(word_boundary_opts, wb);
      }
      if (!silence_weighting_graph.empty() &&
          graphs[g].tag == silence_weighting_graph) {
        silence_weighting_index = g;
      }
    }
    if (!silence_weighting_graph.empty() &&
        graphs[silence_weighting_index].tag != silence_weighting_graph) {
      KALDI_ERR << "--silence-weighting-graph=" << silence_weighting_graph
                << " does not name any of the graphs given";
    }

    std::cout << "READY" << std::endl;

    Shared shared;
    shared.trans_model = &trans_model;
    shared.feature_info = &feature_info;
    shared.decodable_info = &decodable_info;
    shared.frame_subsampling_factor = decodable_opts.frame_subsampling_factor;
    shared.graphs = &graphs;
    shared.silence_weighting_index = silence_weighting_index;
    shared.chunk_length_secs = chunk_length_secs;

    std::map<std::string, std::unique_ptr<Stream>> streams;
    uint64 opened = 0;
    std::vector<char> bytes;
    std::vector<BaseFloat> samples;

    std::string request;
    while (std::getline(std::cin, request)) {
      if (request.empty()) {
        continue;
      }
      std::string id, tags;
      if (request.compare(0, 6, "AUDIO ") == 0) {
        std::istringstream fields(request.substr(6));
        size_t n = 0;
        fields >> id >> n;
        bytes.resize(n);
        if (n > 0 && !std::cin.read(bytes.data(), n)) {
          break;  // the client has gone
        }
        auto found = streams.find(id);
        if (found == streams.end()) {
          continue;  // dropped, or too many: its CLOSE says so
        }
        samples.resize(n / 2);
        for (size_t i = 0; i < n / 2; i++) {
          // s16le, as a wav file's samples are read: unscaled.
          samples[i] = static_cast<int16>(
              static_cast<uint16>(static_cast<unsigned char>(bytes[2 * i])) |
              static_cast<uint16>(static_cast<unsigned char>(bytes[2 * i + 1])) << 8);
        }
        Feed(shared, found->second.get(), samples.data(), samples.size());
        continue;
      }
      if (request.compare(0, 5, "OPEN ") == 0) {
        IdAndTags(request.substr(5), &id, &tags);
        std::unique_ptr<Stream> stream(new Stream());
        stream->active = ActiveGraphs(graphs, tags);
        stream->opened = ++opened;
        try {
          Open(shared, stream.get());
        } catch (const std::exception &e) {
          stream->error = e.what();
        }
        streams[id] = std::move(stream);
        if (streams.size() > kMostStreams) {
          auto oldest = streams.begin();
          for (auto it = streams.begin(); it != streams.end(); ++it) {
            if (it->second->opened < oldest->second->opened) {
              oldest = it;
            }
          }
          streams.erase(oldest);
        }
        continue;
      }
      if (request.compare(0, 6, "CLOSE ") == 0) {
        IdAndTags(request.substr(6), &id, &tags);
        auto found = streams.find(id);
        if (found == streams.end()) {
          std::vector<std::string> lines;
          for (int32 g : ActiveGraphs(graphs, tags)) {
            lines.push_back("ERR " + graphs[g].tag + " unknown stream");
          }
          Answer(lines);
          continue;
        }
        std::unique_ptr<Stream> stream = std::move(found->second);
        streams.erase(found);
        Answer(Finish(shared, stream.get()));
        continue;
      }
      if (request.compare(0, 5, "DROP ") == 0) {
        streams.erase(request.substr(5));
        continue;
      }

      // A wav file, decoded whole: the graphs every one, or those tagged
      // after a tab (one caller's decodes needing only some of them -- the
      // acoustic model still runs once, and the others' searches are
      // skipped).
      const size_t tab = request.find('\t');
      const std::string wav_path = request.substr(0, tab);
      Stream stream;
      stream.active =
          ActiveGraphs(graphs, tab == std::string::npos ? "" : request.substr(tab + 1));
      try {
        // NOTE: MFCC dither draws from the process-global rand() stream, so
        // consecutive requests see different dither than fresh subprocesses
        // would. Measured effect: <= ~0.45 total best-path cost (~0.02 lr
        // units) -- an order of magnitude below the verifier's decision
        // gaps, and the calibration data carries the same dither noise.
        WaveData wave_data;
        {
          Input ki(wav_path);
          wave_data.Read(ki.Stream());
        }
        SubVector<BaseFloat> data(wave_data.Data(), 0);
        stream.samp_freq = wave_data.SampFreq();
        Open(shared, &stream);
        Feed(shared, &stream, data.Data(), data.Dim());
      } catch (const std::exception &e) {
        stream.error = e.what();
      }
      if (!stream.error.empty() || stream.decoders.empty()) {
        std::vector<std::string> lines;
        for (int32 g : stream.active) {
          lines.push_back("ERR " + graphs[g].tag + " " +
                          (stream.error.empty() ? "no result" : stream.error));
        }
        Answer(lines);
        continue;
      }
      Answer(Finish(shared, &stream));
    }

    for (int32 g = 0; g < num_graphs; g++) {
      delete graphs[g].word_boundary;
      delete graphs[g].fst;
    }
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << std::endl;
    return 1;
  }
}
