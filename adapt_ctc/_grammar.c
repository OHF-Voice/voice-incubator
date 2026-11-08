/*
 * Native search for adapt_ctc/grammar.py: the best path through a grammar's
 * token graph over CTC log-probabilities, frame by frame, with a beam. The
 * same search as grammar._decode_python, which the tests hold it to.
 *
 * A path is either in an arc, saying its piece (held for as many frames as
 * it lasts), or at a state, saying blank. Between frames, paths arrive at
 * states, cross the arcs without pieces, and each state keeps its best path
 * and its best whose last piece was another (the same piece twice needs a
 * blank between). Built against the CPython limited API (abi3, 3.11+ for
 * the buffer protocol) and without numpy headers, as _viterbi.c is.
 *
 *   decode(out_ptr i32[S+1], arc_dst i32[A], arc_token i32[A],
 *          arc_cost f32[A], arc_label i32[A], final u8[S],
 *          log_probs f32[T*V], blank f32[T], S, A, T, V, beam, max_active)
 *     -> (score, [labels], [frames]) or None
 *
 * Each label comes with the frame its arc's piece was first said in.
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

typedef struct {
  Py_buffer view;
  int held;
} Buf;

static int get(PyObject *obj, Buf *buf, Py_ssize_t min_bytes, const char *name) {
  if (PyObject_GetBuffer(obj, &buf->view, PyBUF_C_CONTIGUOUS) != 0) return -1;
  buf->held = 1;
  if (buf->view.len < min_bytes) {
    PyErr_Format(PyExc_ValueError, "%s is too small", name);
    return -1;
  }
  return 0;
}

typedef struct {
  int32_t id;
  float score;
  int32_t hist;
  int32_t label; /* a word to add to the history if the path is kept */
} Path;

typedef struct {
  float s1, s2;
  int32_t h1, h2, k1, k2; /* k: the last piece said, -1 for blank */
} Arrival;

typedef struct {
  /* the graph */
  const int32_t *out_ptr, *dst, *token, *label;
  const float *cost;
  const uint8_t *final;
  int32_t S, A;
  /* arrivals between frames */
  Arrival *arr;
  int32_t *arr_stamp, *arrived, *stack;
  uint8_t *in_stack;
  int32_t n_arrived, n_stack, stamp;
  /* paths at a frame, one per arc or state */
  Path *cur_e, *cur_b, *new_e, *new_b;
  int32_t n_cur_e, n_cur_b, n_new_e, n_new_b, cap_e, cap_b;
  int32_t *e_slot, *e_stamp, *b_slot, *b_stamp;
  /* words said, as a tree, with the frame each began in */
  int32_t *h_label, *h_parent, *h_frame;
  int32_t n_hist, cap_hist;
  float *scratch;
  int failed;
} Search;

static void *grow(void *p, int32_t *cap, size_t size) {
  int32_t next = *cap ? *cap * 2 : 1024;
  void *q = realloc(p, (size_t)next * size);
  if (q) *cap = next;
  return q;
}

static void arrive(Search *s, int32_t state, float score, int32_t hist, int32_t last) {
  Arrival *r = &s->arr[state];
  int changed = 0;
  if (s->arr_stamp[state] != s->stamp) {
    s->arr_stamp[state] = s->stamp;
    r->s1 = score, r->h1 = hist, r->k1 = last;
    r->s2 = -INFINITY, r->h2 = -1, r->k2 = -2;
    s->arrived[s->n_arrived++] = state;
    changed = 1;
  } else if (last == r->k1) {
    if (score > r->s1) r->s1 = score, r->h1 = hist, changed = 1;
  } else if (score > r->s1) {
    r->s2 = r->s1, r->h2 = r->h1, r->k2 = r->k1;
    r->s1 = score, r->h1 = hist, r->k1 = last;
    changed = 1;
  } else if (score > r->s2) {
    r->s2 = score, r->h2 = hist, r->k2 = last;
    changed = 1;
  }
  if (changed && !s->in_stack[state]) {
    s->in_stack[state] = 1;
    s->stack[s->n_stack++] = state;
  }
}

/* Where the paths are between frames: at each arc's end, or at their state;
 * then across the arcs without pieces. */
static void arrivals(Search *s, int first) {
  s->stamp++;
  s->n_arrived = 0;
  if (first) arrive(s, 0, 0.0f, -1, -1);
  for (int32_t i = 0; i < s->n_cur_e; i++) {
    Path *p = &s->cur_e[i];
    arrive(s, s->dst[p->id], p->score, p->hist, s->token[p->id]);
  }
  for (int32_t i = 0; i < s->n_cur_b; i++) {
    Path *p = &s->cur_b[i];
    arrive(s, p->id, p->score, p->hist, -1);
  }
  while (s->n_stack) {
    int32_t state = s->stack[--s->n_stack];
    s->in_stack[state] = 0;
    for (int32_t a = s->out_ptr[state]; a < s->out_ptr[state + 1]; a++) {
      if (s->token[a] >= 0) continue;
      Arrival r = s->arr[state];
      arrive(s, s->dst[a], r.s1 - s->cost[a], r.h1, r.k1);
      if (r.k2 != -2) arrive(s, s->dst[a], r.s2 - s->cost[a], r.h2, r.k2);
    }
  }
}

static void add_e(Search *s, int32_t arc, float score, int32_t hist, int32_t label) {
  if (s->e_stamp[arc] == s->stamp) {
    Path *p = &s->new_e[s->e_slot[arc]];
    if (score > p->score) p->score = score, p->hist = hist, p->label = label;
    return;
  }
  if (s->n_new_e == s->cap_e) {
    int32_t cap = s->cap_e;
    Path *grown = grow(s->new_e, &cap, sizeof(Path));
    Path *grown_cur = grown ? realloc(s->cur_e, (size_t)cap * sizeof(Path)) : NULL;
    if (!grown || !grown_cur) {
      if (grown) s->new_e = grown;
      s->failed = 1;
      return;
    }
    s->new_e = grown, s->cur_e = grown_cur, s->cap_e = cap;
  }
  s->e_stamp[arc] = s->stamp;
  s->e_slot[arc] = s->n_new_e;
  s->new_e[s->n_new_e++] = (Path){arc, score, hist, label};
}

static void add_b(Search *s, int32_t state, float score, int32_t hist) {
  if (s->b_stamp[state] == s->stamp) {
    Path *p = &s->new_b[s->b_slot[state]];
    if (score > p->score) p->score = score, p->hist = hist;
    return;
  }
  if (s->n_new_b == s->cap_b) {
    int32_t cap = s->cap_b;
    Path *grown = grow(s->new_b, &cap, sizeof(Path));
    Path *grown_cur = grown ? realloc(s->cur_b, (size_t)cap * sizeof(Path)) : NULL;
    if (!grown || !grown_cur) {
      if (grown) s->new_b = grown;
      s->failed = 1;
      return;
    }
    s->new_b = grown, s->cur_b = grown_cur, s->cap_b = cap;
  }
  s->b_stamp[state] = s->stamp;
  s->b_slot[state] = s->n_new_b;
  s->new_b[s->n_new_b++] = (Path){state, score, hist, 0};
}

/* The max_active-th best score (scratch holds every score). */
static float kth(float *v, int32_t n, int32_t k) {
  /* quickselect for the k-th largest (0-based k) */
  int32_t lo = 0, hi = n - 1;
  while (lo < hi) {
    float pivot = v[(lo + hi) / 2];
    int32_t i = lo, j = hi;
    while (i <= j) {
      while (v[i] > pivot) i++;
      while (v[j] < pivot) j--;
      if (i <= j) {
        float t = v[i];
        v[i] = v[j], v[j] = t;
        i++, j--;
      }
    }
    if (k <= j) hi = j;
    else if (k >= i) lo = i;
    else break;
  }
  return v[k];
}

static int32_t push_hist(Search *s, int32_t label, int32_t parent, int32_t t) {
  if (s->n_hist == s->cap_hist) {
    int32_t cap = s->cap_hist;
    int32_t *l = grow(s->h_label, &cap, sizeof(int32_t));
    if (!l) return s->failed = 1, parent;
    s->h_label = l;
    int32_t *p = realloc(s->h_parent, (size_t)cap * sizeof(int32_t));
    if (!p) return s->failed = 1, parent;
    s->h_parent = p;
    int32_t *f = realloc(s->h_frame, (size_t)cap * sizeof(int32_t));
    if (!f) return s->failed = 1, parent;
    s->h_frame = f, s->cap_hist = cap;
  }
  s->h_label[s->n_hist] = label;
  s->h_parent[s->n_hist] = parent;
  s->h_frame[s->n_hist] = t;
  return s->n_hist++;
}

/* One frame: paths stay in their arc, or leave the states they arrived at
 * by blank or by an arc's piece; then the beam. 0 when none is left. */
static int frame(Search *s, int32_t t, const float *lp, float blank, float beam, int32_t max_active) {
  s->stamp++;
  s->n_new_e = s->n_new_b = 0;
  for (int32_t i = 0; i < s->n_cur_e && !s->failed; i++) {
    Path *p = &s->cur_e[i];
    add_e(s, p->id, p->score + lp[s->token[p->id]], p->hist, 0);
  }
  for (int32_t i = 0; i < s->n_arrived && !s->failed; i++) {
    int32_t state = s->arrived[i];
    Arrival *r = &s->arr[state];
    add_b(s, state, r->s1 + blank, r->h1);
    for (int32_t a = s->out_ptr[state]; a < s->out_ptr[state + 1]; a++) {
      int32_t piece = s->token[a];
      if (piece < 0) continue;
      float base;
      int32_t hist;
      if (r->k1 != piece) base = r->s1, hist = r->h1;
      else if (r->k2 != -2) base = r->s2, hist = r->h2;
      else continue;
      add_e(s, a, base - s->cost[a] + lp[piece], hist, s->label[a]);
    }
  }
  if (s->failed) return 0;
  int32_t n = s->n_new_e + s->n_new_b;
  if (!n) return 0;
  float best = -INFINITY;
  for (int32_t i = 0; i < s->n_new_e; i++) best = fmaxf(best, s->new_e[i].score);
  for (int32_t i = 0; i < s->n_new_b; i++) best = fmaxf(best, s->new_b[i].score);
  float bound = best - beam;
  if (n > max_active) {
    float *v = realloc(s->scratch, (size_t)n * sizeof(float));
    if (!v) return s->failed = 1, 0;
    s->scratch = v;
    for (int32_t i = 0; i < s->n_new_e; i++) v[i] = s->new_e[i].score;
    for (int32_t i = 0; i < s->n_new_b; i++) v[s->n_new_e + i] = s->new_b[i].score;
    bound = fmaxf(bound, kth(v, n, max_active - 1));
  }
  s->n_cur_e = s->n_cur_b = 0;
  for (int32_t i = 0; i < s->n_new_e; i++) {
    Path p = s->new_e[i];
    if (p.score < bound) continue;
    if (p.label) p.hist = push_hist(s, p.label, p.hist, t), p.label = 0;
    s->cur_e[s->n_cur_e++] = p;
  }
  for (int32_t i = 0; i < s->n_new_b; i++)
    if (s->new_b[i].score >= bound) s->cur_b[s->n_cur_b++] = s->new_b[i];
  return !s->failed && (s->n_cur_e + s->n_cur_b) > 0;
}

static void release(Search *s) {
  free(s->arr), free(s->arr_stamp), free(s->arrived), free(s->stack), free(s->in_stack);
  free(s->cur_e), free(s->cur_b), free(s->new_e), free(s->new_b);
  free(s->e_slot), free(s->e_stamp), free(s->b_slot), free(s->b_stamp);
  free(s->h_label), free(s->h_parent), free(s->h_frame), free(s->scratch);
}

static PyObject *py_decode(PyObject *self, PyObject *args) {
  PyObject *o[8];
  int S, A, T, V, max_active;
  double beam;
  (void)self;
  if (!PyArg_ParseTuple(args, "OOOOOOOOiiiidi", &o[0], &o[1], &o[2], &o[3], &o[4], &o[5], &o[6], &o[7],
                        &S, &A, &T, &V, &beam, &max_active))
    return NULL;
  if (S < 1 || A < 0 || T < 0 || V < 1 || max_active < 1) {
    PyErr_SetString(PyExc_ValueError, "bad dimensions");
    return NULL;
  }
  Buf b[8];
  memset(b, 0, sizeof(b));
  const Py_ssize_t need[8] = {
      (Py_ssize_t)(S + 1) * 4, (Py_ssize_t)A * 4, (Py_ssize_t)A * 4, (Py_ssize_t)A * 4,
      (Py_ssize_t)A * 4,       (Py_ssize_t)S,     (Py_ssize_t)T * V * 4, (Py_ssize_t)T * 4};
  const char *names[8] = {"out_ptr", "arc_dst", "arc_token", "arc_cost", "arc_label", "final", "log_probs", "blank"};
  PyObject *result = NULL;
  for (int i = 0; i < 8; i++)
    if (get(o[i], &b[i], need[i], names[i]) != 0) goto done;

  Search s;
  memset(&s, 0, sizeof(s));
  s.out_ptr = b[0].view.buf, s.dst = b[1].view.buf, s.token = b[2].view.buf;
  s.cost = b[3].view.buf, s.label = b[4].view.buf, s.final = b[5].view.buf;
  s.S = S, s.A = A;
  const float *lp = b[6].view.buf, *blank = b[7].view.buf;
  s.arr = malloc((size_t)S * sizeof(Arrival));
  s.arr_stamp = calloc((size_t)S, sizeof(int32_t));
  s.arrived = malloc((size_t)S * sizeof(int32_t));
  s.stack = malloc((size_t)S * sizeof(int32_t));
  s.in_stack = calloc((size_t)S, 1);
  s.e_slot = malloc((size_t)(A ? A : 1) * sizeof(int32_t));
  s.e_stamp = calloc((size_t)(A ? A : 1), sizeof(int32_t));
  s.b_slot = malloc((size_t)S * sizeof(int32_t));
  s.b_stamp = calloc((size_t)S, sizeof(int32_t));
  if (!s.arr || !s.arr_stamp || !s.arrived || !s.stack || !s.in_stack || !s.e_slot || !s.e_stamp || !s.b_slot ||
      !s.b_stamp) {
    release(&s);
    PyErr_NoMemory();
    goto done;
  }

  int alive = 1;
  float best = -INFINITY;
  int32_t best_hist = -1;
  Py_BEGIN_ALLOW_THREADS;
  for (int32_t t = 0; t < T && alive; t++) {
    arrivals(&s, t == 0);
    alive = frame(&s, t, lp + (size_t)t * V, blank[t], (float)beam, max_active);
  }
  if (alive && !s.failed) {
    arrivals(&s, T == 0);
    for (int32_t i = 0; i < s.n_arrived; i++) {
      int32_t state = s.arrived[i];
      if (s.final[state] && s.arr[state].s1 > best) best = s.arr[state].s1, best_hist = s.arr[state].h1;
    }
  }
  Py_END_ALLOW_THREADS;

  if (s.failed) {
    release(&s);
    PyErr_NoMemory();
    goto done;
  }
  if (best == -INFINITY) {
    release(&s);
    result = Py_None;
    Py_INCREF(result);
    goto done;
  }
  int32_t count = 0;
  for (int32_t h = best_hist; h >= 0; h = s.h_parent[h]) count++;
  PyObject *labels = PyList_New(count), *frames = PyList_New(count);
  if (labels && frames) {
    int32_t i = count;
    for (int32_t h = best_hist; h >= 0; h = s.h_parent[h]) {
      --i;
      PyList_SetItem(labels, i, PyLong_FromLong(s.h_label[h]));
      PyList_SetItem(frames, i, PyLong_FromLong(s.h_frame[h]));
    }
    result = Py_BuildValue("(dNN)", (double)best, labels, frames);
  } else {
    Py_XDECREF(labels);
    Py_XDECREF(frames);
  }
  release(&s);
done:
  for (int i = 0; i < 8; i++)
    if (b[i].held) PyBuffer_Release(&b[i].view);
  return result;
}

static PyMethodDef methods[] = {
    {"decode", py_decode, METH_VARARGS, "The best path through a grammar's token graph."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_grammar", NULL, -1, methods,
};

PyMODINIT_FUNC PyInit__grammar(void) { return PyModule_Create(&module); }
