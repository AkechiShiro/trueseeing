from __future__ import annotations
from typing import TYPE_CHECKING

import re
from collections import deque

from trueseeing.core.code.model import Op, Annotation, Param
from trueseeing.core.ui import ui

if TYPE_CHECKING:
  from typing import Iterable, Optional, Type, List, Tuple, TypeVar, Set
  from types import TracebackType
  from trueseeing.core.store import Store

  T = TypeVar('T')

class SmaliAnalyzer:
  _store: Store
  def __init__(self, store: Store) -> None:
    self._store = store

  def __enter__(self) -> SmaliAnalyzer:
    return self

  def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException], traceback: Optional[TracebackType]) -> None:
    pass

  def analyze(self) -> None:
    import time
    from trueseeing.core.literalquery import Query
    analyzed_ops = 0
    analyzed_methods = 0
    analyzed_classes = 0
    started = time.time()

    classmap: Set[Tuple[int, int]] = set()

    begin_at = started
    with self._store.db as c:
      c.execute('begin exclusive')
      base_id = 1
      last_seen = analyzed_ops

      for _, f in Query(c=c).file_enum('smali/%.smali'):
        ops = []
        for op in P.parsed_flat(f.decode('utf-8')):
          analyzed_ops += 1
          if op.eq('directive', 'line'):
            continue
          if op.t == 'annotation' or op.t == 'param':
            continue
          for idx, o in enumerate(tuple([op] + op.p)):
            o._idx = idx
            ops.append(o)
        for t in ops:
          t._id = base_id
          base_id += 1
        self._store.op_store_ops(ops, c=c)

        start = None
        for t in ops:
          if t.eq('directive', 'class'):
            start = t._id
            break
        if start:
          classmap.add(tuple([start, ops[-1]._id])) # type: ignore[arg-type]

        if ui.is_tty():
          if analyzed_ops - last_seen > 65536:
            elapsed = time.time() - begin_at
            ui.info(f"analyze: {analyzed_ops} ops... ({analyzed_ops / elapsed:.02f} ops/s){' '*20}", nl=False, ow=True)
            last_seen = analyzed_ops
        else:
          if analyzed_ops - last_seen > 131072:
            elapsed = time.time() - begin_at
            ui.info(f"analyze: ... {analyzed_ops} ops")
            last_seen = analyzed_ops

      analyzed_ops = self._store.op_count_ops(c=c)

      if ui.is_tty():
        ui.info(f"analyze: {analyzed_ops} ops, classes... {' '*20}", nl=False, ow=True)
      else:
        ui.info(f"analyze: ops: {analyzed_ops}")

      analyzed_classes = self._store.op_store_classmap(classmap, c=c)

      if ui.is_tty():
        ui.info(f"analyze: {analyzed_ops} ops, {analyzed_classes} classes, methods...{' '*20}", nl=False, ow=True)
      else:
        ui.info(f"analyze: classes: {analyzed_classes}")
      analyzed_methods = self._store.op_generate_methodmap(c=c)

    if ui.is_tty():
      ui.info(f"analyze: {analyzed_ops} ops, {analyzed_classes} classes, {analyzed_methods} methods.{' '*20}", ow=True)
    else:
      ui.info(f"analyze: methods: {analyzed_methods}")
    ui.info("analyze: finalizing")
    self._store.op_finalize()
    ui.info(f"analyze: done ({time.time() - started:.02f} sec)")

class P:
  @classmethod
  def parsed_flat(cls, s: str) -> Iterable[Op]:
    q = deque(re.split(r'\n+', s))
    while q:
      l = q.popleft()
      if l:
        t = cls._parsed_as_op(l)
        if t.eq('directive', 'annotation'):
          yield Annotation(t.v, t.p, P._parsed_as_annotation_content(q))
        elif t.eq('directive', 'param'):
          assert t.p
          if len(t.p) == 1:
            yield Param(t.v, t.p, P._parsed_as_param_content(q))
          else:
            # XXX: treat somewhat old-style params as ordinal directives (i.e. describe only their names; no annotations)
            yield t
        else:
          yield t

  @classmethod
  def _head_and_tail(cls, xs: List[T]) -> Tuple[T, Optional[List[T]]]:
    try:
      return xs[0], xs[1:]
    except IndexError:
      return xs[0], None

  @classmethod
  def _parsed_as_op(cls, l: str) -> Op:
    x, xs = cls._head_and_tail(list(P._lexed_as_smali(l)))
    if xs: x.p = xs
    return x

  @classmethod
  def _parsed_as_annotation_content(cls, q: deque[str]) -> List[str]:
    content = []
    try:
      while '.end annotation' not in q[0]:
        content.append(q.popleft())
    except IndexError:
      pass
    return content

  @classmethod
  def _parsed_as_param_content(cls, q: deque[str]) -> List[str]:
    content = []
    try:
      while '.end param' not in q[0]:
        content.append(q.popleft())
    except IndexError:
      pass
    return content

  @classmethod
  def _lexed_as_smali(cls, l: str) -> Iterable[Op]:
    for m in re.finditer(r':(?P<label>[a-z0-9_-]+)|{\s*(?P<multilabel>(?::[a-z0-9_-]+(?: .. )*)+\s*)}|\.(?P<directive>[a-z0-9_-]+)|"(?P<string>.*)"|#(?P<comment>.*)|(?P<reg>[vp][0-9]+)|{(?P<multireg>[vp0-9,. ]+)}|(?P<id>[a-z][a-z/-]*[a-z0-9/-]*)|(?P<reflike>[^ ]+)', l):
      key = m.lastgroup
      if key:
        value = m.group(key)
        if key == 'reflike' and value == ',':
          pass
        else:
          yield Op(key, value)
