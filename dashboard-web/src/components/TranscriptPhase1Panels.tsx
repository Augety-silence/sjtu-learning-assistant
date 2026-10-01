import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import type {
  Phase1ArtifactRead,
  Phase1CorrectionChange,
  Phase1TermCandidate,
  Phase1UncertainSpan,
} from "@/lib/types";

const MAX_CORRECTED_CHARACTERS = 100_000;
const MAX_CHANGES = 80;
const MAX_UNCERTAIN = 60;
const MAX_CANDIDATES = 6;
const MAX_METADATA_ITEMS = 6;

type Phase1PanelKind =
  | "corrected"
  | "correction_diff"
  | "uncertain"
  | "quality";

type UnknownRecord = Record<string, unknown>;
type ParsedUncertainSpan = Phase1UncertainSpan & { candidateTotal: number };
type LearningSummary = {
  knowledgePoints: string[];
  classroomExamples: string[];
  auxiliaryTraining: string[];
};

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null;
}

function asNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown) {
  return typeof value === "boolean" ? value : null;
}

function asStringArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter(
        (item): item is string =>
          typeof item === "string" && Boolean(item.trim()),
      )
    : [];
}

function readLearningItems(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value == null ? [] : [value];
  return values
    .map((item) => {
      if (typeof item === "string") return item.trim();
      const row = asRecord(item);
      if (!row) return "";
      const heading =
        asString(row.title) ?? asString(row.name) ?? asString(row.topic);
      const body =
        asString(row.content) ??
        asString(row.text) ??
        asString(row.description) ??
        asString(row.example) ??
        asString(row.question) ??
        asString(row.prompt);
      if (heading && body) return `**${heading}**\n\n${body}`;
      return heading ?? body ?? "";
    })
    .filter(Boolean);
}

function firstLearningItems(record: UnknownRecord, keys: string[]) {
  for (const key of keys) {
    const items = readLearningItems(record[key]);
    if (items.length) return items;
  }
  return [];
}

function parseLearningSummary(content: string): LearningSummary {
  let source: UnknownRecord | null = null;
  try {
    source = asRecord(JSON.parse(content));
  } catch {
    source = null;
  }
  if (!source) {
    return {
      knowledgePoints: content.trim() ? [content.trim()] : [],
      classroomExamples: [],
      auxiliaryTraining: [],
    };
  }
  const nested =
    asRecord(source.learning_flow) ??
    asRecord(source.learningFlow) ??
    asRecord(source.summary) ??
    source;
  const knowledgePoints = firstLearningItems(nested, [
    "knowledge_points",
    "knowledgePoints",
    "key_points",
    "keyPoints",
    "points",
  ]);
  return {
    knowledgePoints: knowledgePoints.length
      ? knowledgePoints
      : readLearningItems(source.summary ?? source.content ?? source.text),
    classroomExamples: firstLearningItems(nested, [
      "classroom_examples",
      "classroomExamples",
      "class_examples",
      "examples",
    ]),
    auxiliaryTraining: firstLearningItems(nested, [
      "auxiliary_training",
      "auxiliaryTraining",
      "training",
      "practice",
      "exercises",
    ]),
  };
}

function LearningItems({ items }: { items: string[] }) {
  return (
    <div className="learning-flow-items">
      {items.map((item, index) => (
        <div
          className="ai-markdown transcript-markdown"
          key={`${index}:${item.slice(0, 24)}`}
        >
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSanitize]}
          >
            {item}
          </ReactMarkdown>
        </div>
      ))}
    </div>
  );
}

export function TranscriptLearningFlow({ content }: { content: string }) {
  const summary = parseLearningSummary(content);
  return (
    <div className="learning-flow" aria-label="本节学习流">
      <section aria-labelledby="learning-knowledge-title">
        <h4 id="learning-knowledge-title">知识点</h4>
        {summary.knowledgePoints.length ? (
          <LearningItems items={summary.knowledgePoints} />
        ) : (
          <p className="learning-flow-fallback">
            本节暂未整理出知识点，可先查看规整字幕。
          </p>
        )}
      </section>
      <section aria-labelledby="learning-examples-title">
        <h4 id="learning-examples-title">课堂例子</h4>
        {summary.classroomExamples.length ? (
          <LearningItems items={summary.classroomExamples} />
        ) : (
          <p className="learning-flow-fallback">
            旧版小结未单独标注课堂例子，可结合规整字幕回看讲解。
          </p>
        )}
      </section>
      <section aria-labelledby="learning-training-title">
        <h4 id="learning-training-title">辅助训练</h4>
        {summary.auxiliaryTraining.length ? (
          <LearningItems items={summary.auxiliaryTraining} />
        ) : (
          <p className="learning-flow-fallback">
            暂无配套训练，建议根据知识点完成一次复述或自测。
          </p>
        )}
      </section>
    </div>
  );
}

function artifactData(artifact: Phase1ArtifactRead): unknown {
  if (artifact.data !== undefined) return artifact.data;
  try {
    const parsed: unknown = JSON.parse(artifact.content);
    return parsed;
  } catch {
    return null;
  }
}

function readChange(value: unknown): Phase1CorrectionChange | null {
  const row = asRecord(value);
  if (!row) return null;
  const original = asString(row.original);
  const corrected = asString(row.corrected);
  if (!original && !corrected) return null;
  return {
    original: original ?? "（未提供原文）",
    corrected: corrected ?? "（未提供校对文本）",
    type: asString(row.type) ?? "未分类",
    confidence: asNumber(row.confidence) ?? Number.NaN,
    reason: asString(row.reason) ?? "未提供修改原因",
    start: asNumber(row.start) ?? 0,
    end: asNumber(row.end) ?? 0,
    cue_ids: asStringArray(row.cue_ids),
    evidence: asStringArray(row.evidence),
  };
}

function readCandidate(value: unknown): Phase1TermCandidate | null {
  const row = asRecord(value);
  if (!row) return null;
  const canonical = asString(row.canonical);
  if (!canonical) return null;
  return {
    original: asString(row.original) ?? "（未提供原词）",
    canonical,
    category: asString(row.category) ?? "未分类",
    confidence: asNumber(row.confidence) ?? Number.NaN,
    source: asString(row.source) ?? "未提供来源",
    cue_ids: asStringArray(row.cue_ids),
    evidence: asStringArray(row.evidence),
  };
}

function readUncertain(value: unknown): ParsedUncertainSpan | null {
  const row = asRecord(value);
  const text = row ? asString(row.text) : null;
  if (!row || !text) return null;
  const candidates = Array.isArray(row.candidates) ? row.candidates : [];
  return {
    text,
    candidates: candidates
      .slice(0, MAX_CANDIDATES)
      .map(readCandidate)
      .filter((item): item is Phase1TermCandidate => item !== null),
    candidateTotal: candidates.length,
    confidence: asNumber(row.confidence) ?? Number.NaN,
    start: asNumber(row.start) ?? 0,
    end: asNumber(row.end) ?? 0,
    cue_ids: asStringArray(row.cue_ids),
  };
}

function formatPercent(value: number | null) {
  if (value === null || value < 0 || value > 1) return "未提供";
  return `${Math.round(value * 100)}%`;
}

function Metadata({
  cueIds,
  evidence,
}: {
  cueIds: string[];
  evidence: string[];
}) {
  const cues = cueIds.slice(0, MAX_METADATA_ITEMS);
  const evidenceItems = evidence.slice(0, MAX_METADATA_ITEMS);
  if (!cues.length && !evidenceItems.length) return null;
  return (
    <dl className="phase1-evidence">
      {cues.length > 0 && (
        <div>
          <dt>位置</dt>
          <dd>{cues.join("、")}</dd>
        </div>
      )}
      {evidenceItems.length > 0 && (
        <div>
          <dt>证据</dt>
          <dd>{evidenceItems.join("；")}</dd>
        </div>
      )}
    </dl>
  );
}

function TruncationNotice({ shown, total }: { shown: number; total: number }) {
  if (shown >= total) return null;
  return (
    <p className="phase1-truncation" role="status">
      内容较多，当前显示前 {shown} 条，共 {total} 条。
    </p>
  );
}

function EmptyPanel({ children }: { children: string }) {
  return <p className="phase1-empty">{children}</p>;
}

function CorrectedPanel({ artifact }: { artifact: Phase1ArtifactRead }) {
  const text =
    typeof artifact.content === "string" ? artifact.content.trim() : "";
  if (!text)
    return <EmptyPanel>AI 校对字幕为空，原有字幕仍可正常查看。</EmptyPanel>;
  const truncated = text.length > MAX_CORRECTED_CHARACTERS;
  const preview = truncated ? text.slice(0, MAX_CORRECTED_CHARACTERS) : text;
  return (
    <div className="phase1-corrected">
      <p className="phase1-provenance">
        这是独立生成的校对版本，原始字幕未被覆盖。
      </p>
      <div className="ai-markdown transcript-markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSanitize]}
        >
          {preview}
        </ReactMarkdown>
      </div>
      {truncated && (
        <p className="phase1-truncation" role="status">
          字幕较长，当前仅预览前{" "}
          {MAX_CORRECTED_CHARACTERS.toLocaleString("zh-CN")} 个字符。
        </p>
      )}
    </div>
  );
}

function DiffPanel({ artifact }: { artifact: Phase1ArtifactRead }) {
  const source = artifactData(artifact);
  const rows = Array.isArray(source) ? source : [];
  const changes = rows
    .slice(0, MAX_CHANGES)
    .map(readChange)
    .filter((item): item is Phase1CorrectionChange => item !== null);
  if (!changes.length) return <EmptyPanel>未发现可展示的字幕修改。</EmptyPanel>;
  return (
    <div className="phase1-list-wrap">
      <ol className="phase1-change-list">
        {changes.map((change, index) => (
          <li key={`${index}:${change.start}:${change.original}`}>
            <div className="phase1-change-heading">
              <span className="phase1-change-type">{change.type}</span>
              <span>置信度 {formatPercent(change.confidence)}</span>
            </div>
            <div
              className="phase1-change-copy"
              aria-label={`${change.original} 修改为 ${change.corrected}`}
            >
              <del>{change.original}</del>
              <span aria-hidden="true">→</span>
              <ins>{change.corrected}</ins>
            </div>
            <p>{change.reason}</p>
            <Metadata cueIds={change.cue_ids} evidence={change.evidence} />
          </li>
        ))}
      </ol>
      <TruncationNotice shown={changes.length} total={rows.length} />
    </div>
  );
}

function UncertainPanel({ artifact }: { artifact: Phase1ArtifactRead }) {
  const source = artifactData(artifact);
  const rows = Array.isArray(source) ? source : [];
  const spans = rows
    .slice(0, MAX_UNCERTAIN)
    .map(readUncertain)
    .filter((item): item is ParsedUncertainSpan => item !== null);
  if (!spans.length) return <EmptyPanel>没有需要人工确认的片段。</EmptyPanel>;
  return (
    <div className="phase1-list-wrap">
      <p className="phase1-readonly-note">
        以下内容仅供查看；后续版本将支持逐项确认，本页不会保存任何修改。
      </p>
      <ol className="phase1-uncertain-list">
        {spans.map((span, index) => (
          <li key={`${index}:${span.start}:${span.text}`}>
            <div className="phase1-uncertain-heading">
              <strong>{span.text}</strong>
              <span>当前置信度 {formatPercent(span.confidence)}</span>
            </div>
            {span.candidates.length > 0 ? (
              <ul
                className="phase1-candidate-list"
                aria-label={`${span.text} 的候选项`}
              >
                {span.candidates
                  .slice(0, MAX_CANDIDATES)
                  .map((candidate, candidateIndex) => (
                    <li key={`${candidateIndex}:${candidate.canonical}`}>
                      <div>
                        <strong>{candidate.canonical}</strong>
                        <span>
                          {candidate.category} · 置信度{" "}
                          {formatPercent(candidate.confidence)}
                        </span>
                      </div>
                      <p>来源：{candidate.source}</p>
                      <Metadata
                        cueIds={candidate.cue_ids}
                        evidence={candidate.evidence}
                      />
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="transcript-muted">暂无候选项。</p>
            )}
            {span.candidateTotal > span.candidates.length && (
              <p className="phase1-candidate-truncation">
                候选项较多，仅显示前 {span.candidates.length} 项。
              </p>
            )}
            <Metadata cueIds={span.cue_ids} evidence={[]} />
          </li>
        ))}
      </ol>
      <TruncationNotice shown={spans.length} total={rows.length} />
    </div>
  );
}

function QualityPanel({
  artifact,
  manifestWarnings,
}: {
  artifact: Phase1ArtifactRead;
  manifestWarnings: string[];
}) {
  const quality = asRecord(artifactData(artifact));
  if (!quality)
    return (
      <EmptyPanel>质量报告格式暂不可识别，其他字幕结果不受影响。</EmptyPanel>
    );
  const warnings = [
    ...new Set([...asStringArray(quality.warnings), ...manifestWarnings]),
  ];
  const metrics = [
    [
      "Schema 校验",
      asBoolean(quality.schema_pass) === null
        ? "未提供"
        : asBoolean(quality.schema_pass)
          ? "通过"
          : "未通过",
    ],
    ["Critic 通过率", formatPercent(asNumber(quality.critic_pass_rate))],
    ["待确认占比", formatPercent(asNumber(quality.uncertain_rate))],
    [
      "数字修改",
      asNumber(quality.numeric_change_count)?.toLocaleString("zh-CN") ??
        "未提供",
    ],
    [
      "无证据修改",
      asNumber(quality.unsupported_change_count)?.toLocaleString("zh-CN") ??
        "未提供",
    ],
    ["质量状态", asString(quality.status) ?? "未提供"],
  ];
  return (
    <div className="phase1-quality">
      <dl className="phase1-quality-grid">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <section
        className="phase1-warning-list"
        aria-labelledby="phase1-warning-title"
      >
        <h4 id="phase1-warning-title">Warning</h4>
        {warnings.length ? (
          <ul>
            {warnings.slice(0, 20).map((warning, index) => (
              <li key={`${index}:${warning}`}>{warning}</li>
            ))}
          </ul>
        ) : (
          <p>未报告质量警告。</p>
        )}
      </section>
    </div>
  );
}

export function TranscriptPhase1Panel({
  kind,
  artifact,
  manifestWarnings,
}: {
  kind: Phase1PanelKind;
  artifact: Phase1ArtifactRead;
  manifestWarnings: string[];
}) {
  if (kind === "corrected") return <CorrectedPanel artifact={artifact} />;
  if (kind === "correction_diff") return <DiffPanel artifact={artifact} />;
  if (kind === "uncertain") return <UncertainPanel artifact={artifact} />;
  return (
    <QualityPanel artifact={artifact} manifestWarnings={manifestWarnings} />
  );
}
