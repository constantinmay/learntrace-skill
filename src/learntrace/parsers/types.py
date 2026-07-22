"""静态解析任务线的公共结果类型。"""

from __future__ import annotations

from dataclasses import dataclass

from learntrace.models import ObservableEvent


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """一项未阻断整批解析的明确告警。"""

    code: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """解析出的事实事件及可定位告警。"""

    events: tuple[ObservableEvent, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()

    def merged(self, *others: ParseResult) -> ParseResult:
        events = list(self.events)
        warnings = list(self.warnings)
        for other in others:
            events.extend(other.events)
            warnings.extend(other.warnings)
        return ParseResult(events=tuple(events), warnings=tuple(warnings))
