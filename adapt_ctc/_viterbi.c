/*
 * Native phrase-graph Viterbi: the same recursion as spotter.viterbi_numpy,
 * one tight loop per frame instead of ~30 numpy calls. Results are identical
 * (same float32 operations in the same order, same tie-breaking). There are
 * two searches: the full one below, and viterbi_live further down.
 *
 * Built against the CPython limited API (abi3, 3.11+ for the buffer
 * protocol) and without numpy headers: arrays arrive as contiguous buffers
 * and the outputs are filled in place.
 *
 *   viterbi(arc_token i32[A], arc_src i32[A], from_root u8[A],
 *           in_ptr i32[P+1], in_arcs i32[E], terminals i32[M],
 *           rel_tokens f32[T*V], rel_blank f32[T],
 *           score f32[T*M], start i32[T*M], tokens i32[T*M],
 *           A, P, E, M, T, V) -> None
 *
 * Incoming arcs are in CSR form: position p's are in_arcs[in_ptr[p]..in_ptr[p+1]).
 */

#define PY_SSIZE_T_CLEAN
#ifndef Py_LIMITED_API
#define Py_LIMITED_API 0x030B0000
#endif
#include <Python.h>

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define ROOT 0

typedef struct {
  Py_buffer view;
  int held;
} Buf;

static int get(PyObject *obj, Buf *buf, Py_ssize_t min_bytes, int writable,
               const char *name) {
  int flags = writable ? (PyBUF_C_CONTIGUOUS | PyBUF_WRITABLE) : PyBUF_C_CONTIGUOUS;
  if (PyObject_GetBuffer(obj, &buf->view, flags) != 0) return -1;
  buf->held = 1;
  if (buf->view.len < min_bytes) {
    PyErr_Format(PyExc_ValueError, "%s is too small", name);
    return -1;
  }
  return 0;
}

static PyObject *py_viterbi(PyObject *self, PyObject *args) {
  PyObject *o_tok, *o_src, *o_root, *o_ptr, *o_in, *o_term, *o_rel, *o_blank,
      *o_score, *o_start, *o_tokens;
  int A, P, E, M, T, V;
  (void)self;
  if (!PyArg_ParseTuple(args, "OOOOOOOOOOOiiiiii", &o_tok, &o_src, &o_root,
                        &o_ptr, &o_in, &o_term, &o_rel, &o_blank, &o_score,
                        &o_start, &o_tokens, &A, &P, &E, &M, &T, &V))
    return NULL;
  if (A < 0 || P < 1 || E < 0 || M < 0 || T < 0 || V < 1) {
    PyErr_SetString(PyExc_ValueError, "bad dimensions");
    return NULL;
  }

  Buf b[11];
  memset(b, 0, sizeof(b));
  PyObject *result = NULL;
  float *emit = NULL, *next = NULL, *blank = NULL, *v1 = NULL, *v2 = NULL;
  int32_t *e_start = NULL, *e_tok = NULL, *n_start = NULL, *n_tok = NULL;
  int32_t *b_start = NULL, *b_tok = NULL, *a1 = NULL, *a2 = NULL, *tok1 = NULL;

  if (get(o_tok, &b[0], (Py_ssize_t)A * 4, 0, "arc_token") ||
      get(o_src, &b[1], (Py_ssize_t)A * 4, 0, "arc_src") ||
      get(o_root, &b[2], (Py_ssize_t)A, 0, "from_root") ||
      get(o_ptr, &b[3], (Py_ssize_t)(P + 1) * 4, 0, "in_ptr") ||
      get(o_in, &b[4], (Py_ssize_t)E * 4, 0, "in_arcs") ||
      get(o_term, &b[5], (Py_ssize_t)M * 4, 0, "terminals") ||
      get(o_rel, &b[6], (Py_ssize_t)T * V * 4, 0, "rel_tokens") ||
      get(o_blank, &b[7], (Py_ssize_t)T * 4, 0, "rel_blank") ||
      get(o_score, &b[8], (Py_ssize_t)T * M * 4, 1, "score") ||
      get(o_start, &b[9], (Py_ssize_t)T * M * 4, 1, "start") ||
      get(o_tokens, &b[10], (Py_ssize_t)T * M * 4, 1, "tokens"))
    goto done;

  const int32_t *arc_token = b[0].view.buf, *arc_src = b[1].view.buf;
  const uint8_t *from_root = b[2].view.buf;
  const int32_t *in_ptr = b[3].view.buf, *in_arcs = b[4].view.buf;
  const int32_t *terminals = b[5].view.buf;
  const float *rel = b[6].view.buf, *rel_blank = b[7].view.buf;
  float *score = b[8].view.buf;
  int32_t *start = b[9].view.buf, *tokens = b[10].view.buf;

  /* Index A is a sentinel arc that is never reachable. */
  emit = malloc(sizeof(float) * (A + 1));
  next = malloc(sizeof(float) * (A + 1));
  e_start = calloc(A + 1, sizeof(int32_t));
  e_tok = calloc(A + 1, sizeof(int32_t));
  n_start = calloc(A + 1, sizeof(int32_t));
  n_tok = calloc(A + 1, sizeof(int32_t));
  blank = malloc(sizeof(float) * P);
  b_start = calloc(P, sizeof(int32_t));
  b_tok = calloc(P, sizeof(int32_t));
  v1 = malloc(sizeof(float) * P);
  v2 = malloc(sizeof(float) * P);
  a1 = malloc(sizeof(int32_t) * P);
  a2 = malloc(sizeof(int32_t) * P);
  tok1 = malloc(sizeof(int32_t) * P);
  if (!emit || !next || !e_start || !e_tok || !n_start || !n_tok || !blank ||
      !b_start || !b_tok || !v1 || !v2 || !a1 || !a2 || !tok1) {
    PyErr_NoMemory();
    goto done;
  }
  for (int a = 0; a <= A; ++a) emit[a] = -INFINITY;
  for (int p = 0; p < P; ++p) blank[p] = -INFINITY;

  Py_BEGIN_ALLOW_THREADS;
  for (int t = 0; t < T; ++t) {
    const float *row = rel + (size_t)t * V;

    /* Best arc into each position, and best with a different piece. A
       position with no incoming arc gets the sentinel (piece -1), as the
       padded numpy layout does. */
    for (int p = 0; p < P; ++p) {
      int lo = in_ptr[p], hi = in_ptr[p + 1];
      int32_t best_arc = A, best_tok = -1;
      float best = -INFINITY;
      for (int e = lo; e < hi; ++e) {
        int32_t arc = in_arcs[e];
        if (e == lo || emit[arc] > best) {
          best = emit[arc];
          best_arc = arc;
          best_tok = arc_token[arc];
        }
      }
      a1[p] = best_arc;
      tok1[p] = best_tok;
      v1[p] = best;
      int32_t second_arc = A;
      float second = -INFINITY;
      int found = 0;
      for (int e = lo; e < hi; ++e) {
        int32_t arc = in_arcs[e];
        float v = arc_token[arc] == best_tok ? -INFINITY : emit[arc];
        if (!found || v > second) {
          second = v;
          second_arc = arc;
          found = 1;
        }
      }
      a2[p] = second_arc;
      v2[p] = second;
    }

    for (int a = 0; a < A; ++a) {
      int p = arc_src[a];
      float best = emit[a];
      int32_t bs = e_start[a], bt = e_tok[a];
      if (blank[p] > best) {
        best = blank[p];
        bs = b_start[p];
        bt = b_tok[p] + 1;
      }
      int same = arc_token[a] == tok1[p];
      float pv = same ? v2[p] : v1[p];
      int32_t pa = same ? a2[p] : a1[p];
      if (pv > best) {
        best = pv;
        bs = e_start[pa];
        bt = e_tok[pa] + 1;
      }
      if (from_root[a] && 0.0f > best) {
        best = 0.0f;
        bs = t;
        bt = 1;
      }
      next[a] = best + row[arc_token[a]];
      n_start[a] = bs;
      n_tok[a] = bt;
    }

    /* Blank after each position, from the previous frame's arcs. */
    for (int p = 0; p < P; ++p) {
      if (v1[p] > blank[p]) {
        blank[p] = v1[p] + rel_blank[t];
        b_start[p] = e_start[a1[p]];
        b_tok[p] = e_tok[a1[p]];
      } else {
        blank[p] = blank[p] + rel_blank[t];
      }
    }
    blank[ROOT] = -INFINITY;

    memcpy(emit, next, sizeof(float) * A);
    memcpy(e_start, n_start, sizeof(int32_t) * A);
    memcpy(e_tok, n_tok, sizeof(int32_t) * A);

    for (int m = 0; m < M; ++m) {
      int p = terminals[m];
      int32_t best_arc = A;
      float best = -INFINITY;
      for (int e = in_ptr[p]; e < in_ptr[p + 1]; ++e) {
        int32_t arc = in_arcs[e];
        if (e == in_ptr[p] || emit[arc] > best) {
          best = emit[arc];
          best_arc = arc;
        }
      }
      size_t out = (size_t)t * M + m;
      score[out] = best;
      start[out] = e_start[best_arc];
      tokens[out] = e_tok[best_arc];
    }
  }
  Py_END_ALLOW_THREADS;

  Py_INCREF(Py_None);
  result = Py_None;

done:
  for (int i = 0; i < 11; ++i)
    if (b[i].held) PyBuffer_Release(&b[i].view);
  free(emit);
  free(next);
  free(e_start);
  free(e_tok);
  free(n_start);
  free(n_tok);
  free(blank);
  free(b_start);
  free(b_tok);
  free(v1);
  free(v2);
  free(a1);
  free(a2);
  free(tok1);
  return result;
}

/*
 * Live-state search: the same recursion, visiting only arcs near a live path.
 *
 * A state (an arc's emitting state, or the blank after a position) is dropped
 * once its cost exceeds gate * (pieces so far + most pieces left to any
 * phrase end): even perfect remaining pieces could not bring it under the
 * gate. Anything derived from a dropped state would be dropped too, so a hit
 * the full search reports within the gate is reported identically here.
 *
 * Dead states hold -inf. Per frame the search visits the arcs that were live,
 * the arcs leaving positions with a live arc in or a live blank, and the
 * phrase-starting arcs whose piece the frame scores well enough (grouped by
 * piece, longest phrase first, so the scan stops at the first that cannot
 * make it).
 *
 *   viterbi_live(arc_token i32[A], arc_src i32[A], arc_dst i32[A],
 *                in_ptr i32[P+1], in_arcs i32[A], out_ptr i32[P+1],
 *                out_arcs i32[A], remaining i32[P], terminal_of i32[P],
 *                root_ptr i32[V+1], root_arcs i32[R], root_reach i32[V],
 *                rel_tokens f32[T*V], rel_blank f32[T],
 *                score f32[T*M], start i32[T*M], tokens i32[T*M],
 *                gate, A, P, M, T, V) -> number of arc visits
 */
static PyObject *py_viterbi_live(PyObject *self, PyObject *args) {
  PyObject *o[17];
  double gate;
  int A, P, M, T, V;
  (void)self;
  if (!PyArg_ParseTuple(args, "OOOOOOOOOOOOOOOOOdiiiii", &o[0], &o[1], &o[2],
                        &o[3], &o[4], &o[5], &o[6], &o[7], &o[8], &o[9], &o[10],
                        &o[11], &o[12], &o[13], &o[14], &o[15], &o[16], &gate,
                        &A, &P, &M, &T, &V))
    return NULL;
  if (A < 0 || P < 1 || M < 0 || T < 0 || V < 1) {
    PyErr_SetString(PyExc_ValueError, "bad dimensions");
    return NULL;
  }

  Buf b[17];
  memset(b, 0, sizeof(b));
  PyObject *result = NULL;
  long long visits = 0;

  /* Arc state, double-buffered by candidate index. */
  float *emit = NULL, *next_val = NULL;
  int32_t *e_start = NULL, *e_tok = NULL, *next_start = NULL, *next_tok = NULL;
  /* Blank state per position. */
  float *blank = NULL;
  int32_t *b_start = NULL, *b_tok = NULL;
  /* Per touched position: best incoming arc, and best with a different piece. */
  float *v1 = NULL, *v2 = NULL;
  int32_t *a1 = NULL, *a2 = NULL, *tok1 = NULL;
  /* Work lists and visit stamps. */
  int32_t *live = NULL, *new_live = NULL, *live_pos = NULL, *new_live_pos = NULL;
  int32_t *touched = NULL, *cand = NULL;
  int32_t *pos_stamp = NULL, *arc_stamp = NULL, *term_stamp = NULL;

  if (get(o[0], &b[0], (Py_ssize_t)A * 4, 0, "arc_token") ||
      get(o[1], &b[1], (Py_ssize_t)A * 4, 0, "arc_src") ||
      get(o[2], &b[2], (Py_ssize_t)A * 4, 0, "arc_dst") ||
      get(o[3], &b[3], (Py_ssize_t)(P + 1) * 4, 0, "in_ptr") ||
      get(o[4], &b[4], (Py_ssize_t)A * 4, 0, "in_arcs") ||
      get(o[5], &b[5], (Py_ssize_t)(P + 1) * 4, 0, "out_ptr") ||
      get(o[6], &b[6], (Py_ssize_t)A * 4, 0, "out_arcs") ||
      get(o[7], &b[7], (Py_ssize_t)P * 4, 0, "remaining") ||
      get(o[8], &b[8], (Py_ssize_t)P * 4, 0, "terminal_of") ||
      get(o[9], &b[9], (Py_ssize_t)(V + 1) * 4, 0, "root_ptr") ||
      get(o[10], &b[10], 0, 0, "root_arcs") ||
      get(o[11], &b[11], (Py_ssize_t)V * 4, 0, "root_reach") ||
      get(o[12], &b[12], (Py_ssize_t)T * V * 4, 0, "rel_tokens") ||
      get(o[13], &b[13], (Py_ssize_t)T * 4, 0, "rel_blank") ||
      get(o[14], &b[14], (Py_ssize_t)T * M * 4, 1, "score") ||
      get(o[15], &b[15], (Py_ssize_t)T * M * 4, 1, "start") ||
      get(o[16], &b[16], (Py_ssize_t)T * M * 4, 1, "tokens"))
    goto done;

  const int32_t *arc_token = b[0].view.buf, *arc_src = b[1].view.buf;
  const int32_t *arc_dst = b[2].view.buf;
  const int32_t *in_ptr = b[3].view.buf, *in_arcs = b[4].view.buf;
  const int32_t *out_ptr = b[5].view.buf, *out_arcs = b[6].view.buf;
  const int32_t *remaining = b[7].view.buf, *terminal_of = b[8].view.buf;
  const int32_t *root_ptr = b[9].view.buf, *root_arcs = b[10].view.buf;
  const int32_t *root_reach = b[11].view.buf;
  const float *rel = b[12].view.buf, *rel_blank = b[13].view.buf;
  float *score = b[14].view.buf;
  int32_t *start = b[15].view.buf, *tokens = b[16].view.buf;
  if (b[10].view.len < (Py_ssize_t)root_ptr[V] * 4) {
    PyErr_SetString(PyExc_ValueError, "root_arcs is too small");
    goto done;
  }

  emit = malloc(sizeof(float) * (A + 1));
  e_start = calloc(A + 1, sizeof(int32_t));
  e_tok = calloc(A + 1, sizeof(int32_t));
  next_val = malloc(sizeof(float) * (A + 1));
  next_start = malloc(sizeof(int32_t) * (A + 1));
  next_tok = malloc(sizeof(int32_t) * (A + 1));
  blank = malloc(sizeof(float) * P);
  b_start = calloc(P, sizeof(int32_t));
  b_tok = calloc(P, sizeof(int32_t));
  v1 = malloc(sizeof(float) * P);
  v2 = malloc(sizeof(float) * P);
  a1 = malloc(sizeof(int32_t) * P);
  a2 = malloc(sizeof(int32_t) * P);
  tok1 = malloc(sizeof(int32_t) * P);
  live = malloc(sizeof(int32_t) * (A + 1));
  new_live = malloc(sizeof(int32_t) * (A + 1));
  live_pos = malloc(sizeof(int32_t) * P);
  new_live_pos = malloc(sizeof(int32_t) * P);
  touched = malloc(sizeof(int32_t) * P);
  cand = malloc(sizeof(int32_t) * (A + 1));
  pos_stamp = calloc(P, sizeof(int32_t));
  arc_stamp = calloc(A + 1, sizeof(int32_t));
  term_stamp = calloc(M + 1, sizeof(int32_t));
  if (!emit || !e_start || !e_tok || !next_val || !next_start || !next_tok ||
      !blank || !b_start || !b_tok || !v1 || !v2 || !a1 || !a2 || !tok1 ||
      !live || !new_live || !live_pos || !new_live_pos || !touched || !cand ||
      !pos_stamp || !arc_stamp || !term_stamp) {
    PyErr_NoMemory();
    goto done;
  }

  Py_BEGIN_ALLOW_THREADS;
  for (int a = 0; a <= A; ++a) emit[a] = -INFINITY;
  for (int p = 0; p < P; ++p) blank[p] = -INFINITY;
  for (size_t i = 0; i < (size_t)T * M; ++i) {
    score[i] = -INFINITY;
    start[i] = 0;
    tokens[i] = 0;
  }
  int n_live = 0, n_live_pos = 0;

  for (int t = 0; t < T; ++t) {
    const int32_t stamp = t + 1;
    const float *row = rel + (size_t)t * V;

    /* 1. Positions with a live arc in or a live blank: best incoming arcs,
          from the previous frame, in CSR order as the full search scans them. */
    int n_touched = 0;
    for (int i = 0; i < n_live; ++i) {
      int p = arc_dst[live[i]];
      if (pos_stamp[p] != stamp) {
        pos_stamp[p] = stamp;
        touched[n_touched++] = p;
      }
    }
    for (int i = 0; i < n_live_pos; ++i) {
      int p = live_pos[i];
      if (pos_stamp[p] != stamp) {
        pos_stamp[p] = stamp;
        touched[n_touched++] = p;
      }
    }
    for (int i = 0; i < n_touched; ++i) {
      int p = touched[i];
      int lo = in_ptr[p], hi = in_ptr[p + 1];
      int32_t best_arc = A, best_tok = -1;
      float best = -INFINITY;
      for (int e = lo; e < hi; ++e) {
        int32_t arc = in_arcs[e];
        if (e == lo || emit[arc] > best) {
          best = emit[arc];
          best_arc = arc;
          best_tok = arc_token[arc];
        }
      }
      a1[p] = best_arc;
      tok1[p] = best_tok;
      v1[p] = best;
      int32_t second_arc = A;
      float second = -INFINITY;
      int found = 0;
      for (int e = lo; e < hi; ++e) {
        int32_t arc = in_arcs[e];
        float v = arc_token[arc] == best_tok ? -INFINITY : emit[arc];
        if (!found || v > second) {
          second = v;
          second_arc = arc;
          found = 1;
        }
      }
      a2[p] = second_arc;
      v2[p] = second;
    }

    /* 2. New arc states, from the previous frame. Only survivors are kept
          (in cand / next_*), so an arc that would die at once costs a
          lookup and a compare. Three sources, each arc evaluated once:
          live arcs (every transition), arcs leaving touched positions (blank
          or previous piece), and phrase starts on pieces this frame scores
          well enough. */
    int n_cand = 0;
#define SURVIVES(value, pieces, arc) \
  ((double)(-(value)) <= gate * (double)((pieces) + remaining[arc_dst[arc]]))
#define KEEP(arc, value, first, pieces) \
  do {                                 \
    cand[n_cand] = (arc);              \
    next_val[n_cand] = (value);        \
    next_start[n_cand] = (first);      \
    next_tok[n_cand] = (pieces);       \
    ++n_cand;                          \
  } while (0)

    for (int i = 0; i < n_live; ++i) arc_stamp[live[i]] = stamp;

    for (int i = 0; i < n_live; ++i) {
      int a = live[i];
      int p = arc_src[a];
      float best = emit[a];
      int32_t bs = e_start[a], bt = e_tok[a];
      if (blank[p] > best) {
        best = blank[p];
        bs = b_start[p];
        bt = b_tok[p] + 1;
      }
      if (pos_stamp[p] == stamp) {
        int same = arc_token[a] == tok1[p];
        float pv = same ? v2[p] : v1[p];
        int32_t pa = same ? a2[p] : a1[p];
        if (pv > best) {
          best = pv;
          bs = e_start[pa];
          bt = e_tok[pa] + 1;
        }
      }
      if (p == ROOT && 0.0f > best) {
        best = 0.0f;
        bs = t;
        bt = 1;
      }
      float nv = best + row[arc_token[a]];
      if (SURVIVES(nv, bt, a)) KEEP(a, nv, bs, bt);
    }
    visits += n_live;

    for (int i = 0; i < n_touched; ++i) {
      int p = touched[i];
      /* Not live, so no held piece; p is never the root. */
      float from_blank = blank[p];
      for (int e = out_ptr[p]; e < out_ptr[p + 1]; ++e) {
        int a = out_arcs[e];
        if (arc_stamp[a] == stamp) continue;
        ++visits;
        float best = -INFINITY;
        int32_t bs = 0, bt = 0;
        if (from_blank > best) {
          best = from_blank;
          bs = b_start[p];
          bt = b_tok[p] + 1;
        }
        int same = arc_token[a] == tok1[p];
        float pv = same ? v2[p] : v1[p];
        int32_t pa = same ? a2[p] : a1[p];
        if (pv > best) {
          best = pv;
          bs = e_start[pa];
          bt = e_tok[pa] + 1;
        }
        float nv = best + row[arc_token[a]];
        if (SURVIVES(nv, bt, a)) KEEP(a, nv, bs, bt);
      }
    }

    for (int v = 0; v < V; ++v) {
      int lo = root_ptr[v], hi = root_ptr[v + 1];
      if (lo == hi) continue;
      double cost = -(double)row[v];
      if (cost > gate * (double)root_reach[v]) continue;
      /* Grouped arcs are sorted longest phrase first: stop at the first
         that cannot finish within the gate. */
      for (int e = lo; e < hi; ++e) {
        int a = root_arcs[e];
        ++visits;
        if (!SURVIVES(row[v], 1, a)) break;
        if (arc_stamp[a] == stamp) continue;
        KEEP(a, 0.0f + row[v], t, 1);
      }
    }
#undef SURVIVES
#undef KEEP

    /* 3. Blank after each touched position, from the previous frame. */
    int n_new_pos = 0;
    for (int i = 0; i < n_touched; ++i) {
      int p = touched[i];
      if (p == ROOT) continue;
      float nb;
      int32_t ns, nt;
      if (v1[p] > blank[p]) {
        nb = v1[p] + rel_blank[t];
        ns = e_start[a1[p]];
        nt = e_tok[a1[p]];
      } else {
        nb = blank[p] + rel_blank[t];
        ns = b_start[p];
        nt = b_tok[p];
      }
      if ((double)(-nb) > gate * (double)(nt + remaining[p])) {
        blank[p] = -INFINITY;
      } else {
        blank[p] = nb;
        b_start[p] = ns;
        b_tok[p] = nt;
        new_live_pos[n_new_pos++] = p;
      }
    }

    /* 4. Commit: the old live arcs die unless they survived. */
    for (int i = 0; i < n_live; ++i) emit[live[i]] = -INFINITY;
    int n_new = 0;
    for (int i = 0; i < n_cand; ++i) {
      int a = cand[i];
      emit[a] = next_val[i];
      e_start[a] = next_start[i];
      e_tok[a] = next_tok[i];
      new_live[n_new++] = a;
    }

    /* 5. Phrase ends reached this frame. */
    for (int i = 0; i < n_new; ++i) {
      int p = arc_dst[new_live[i]];
      int m = terminal_of[p];
      if (m < 0 || term_stamp[m] == stamp) continue;
      term_stamp[m] = stamp;
      int32_t best_arc = A;
      float best = -INFINITY;
      for (int e = in_ptr[p]; e < in_ptr[p + 1]; ++e) {
        int32_t arc = in_arcs[e];
        if (e == in_ptr[p] || emit[arc] > best) {
          best = emit[arc];
          best_arc = arc;
        }
      }
      size_t out = (size_t)t * M + m;
      score[out] = best;
      start[out] = e_start[best_arc];
      tokens[out] = e_tok[best_arc];
    }

    int32_t *swap = live;
    live = new_live;
    new_live = swap;
    n_live = n_new;
    swap = live_pos;
    live_pos = new_live_pos;
    new_live_pos = swap;
    n_live_pos = n_new_pos;
  }
  Py_END_ALLOW_THREADS;

  result = PyLong_FromLongLong(visits);

done:
  for (int i = 0; i < 17; ++i)
    if (b[i].held) PyBuffer_Release(&b[i].view);
  free(emit);
  free(e_start);
  free(e_tok);
  free(next_val);
  free(next_start);
  free(next_tok);
  free(blank);
  free(b_start);
  free(b_tok);
  free(v1);
  free(v2);
  free(a1);
  free(a2);
  free(tok1);
  free(live);
  free(new_live);
  free(live_pos);
  free(new_live_pos);
  free(touched);
  free(cand);
  free(pos_stamp);
  free(arc_stamp);
  free(term_stamp);
  return result;
}

static PyMethodDef methods[] = {
    {"viterbi", py_viterbi, METH_VARARGS, "Phrase-graph Viterbi (fills outputs)."},
    {"viterbi_live", py_viterbi_live, METH_VARARGS,
     "Live-state phrase-graph Viterbi (fills outputs, returns arc visits)."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_viterbi", NULL, -1, methods,
};

PyMODINIT_FUNC PyInit__viterbi(void) { return PyModule_Create(&module); }
