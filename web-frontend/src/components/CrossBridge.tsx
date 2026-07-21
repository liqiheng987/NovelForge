import { useState } from "react";
import { ArrowRight, Languages, LoaderCircle, WandSparkles } from "lucide-react";
import { api } from "../api/client";

export default function CrossBridge({ apiConfig }: { apiConfig: Record<string, string> }) {
  const [sourceText, setSourceText] = useState("");
  const [sourceType, setSourceType] = useState("wuxia");
  const [targetType, setTargetType] = useState("fantasy");
  const [targetLanguage, setTargetLanguage] = useState("zh");
  const [result, setResult] = useState<{ bridged_content?: string; mapping_table?: Array<{ source: string; target: string; reason?: string }>; translation_table?: Array<{ source: string; target: string }> } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!sourceText.trim()) return;
    setBusy(true);
    try { setResult(await api("/cross/bridge", { method: "POST", body: JSON.stringify({ source_text: sourceText, source_type: sourceType, target_type: targetType, source_language: "zh", target_language: targetLanguage, api_config: apiConfig }) })); } finally { setBusy(false); }
  };
  return (
    <section className="cross-bridge">
      <header className="cross-bridge-header"><Languages size={14} /><div><strong>跨体裁 / 跨语言转译</strong><span>保留核心设定，转换表达体系</span></div></header>
      <div className="cross-route-grid">
        <label className="cross-field"><span>源体裁</span><input value={sourceType} onChange={(event) => setSourceType(event.target.value)} placeholder="如：武侠" /></label>
        <ArrowRight className="cross-route-arrow" size={14} />
        <label className="cross-field"><span>目标体裁</span><input value={targetType} onChange={(event) => setTargetType(event.target.value)} placeholder="如：奇幻" /></label>
        <label className="cross-field cross-language"><span>输出语言</span><select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)}><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
      </div>
      <label className="cross-field cross-source-field"><span>待转译片段</span><textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder="粘贴需要转换体裁或语言的素材片段…" /></label>
      <button className="cross-run-button" disabled={busy || !sourceText.trim()} type="button" onClick={() => void run()}>{busy ? <LoaderCircle className="spin" size={14} /> : <WandSparkles size={14} />}{busy ? "正在转译…" : "生成转译方案"}</button>
      {result?.bridged_content && <article className="cross-result"><header><WandSparkles size={13} /><strong>转译结果</strong></header><p>{result.bridged_content}</p>{(result.mapping_table ?? result.translation_table ?? []).length > 0 && <ul>{(result.mapping_table ?? result.translation_table ?? []).map((item, index) => <li key={index}><span>{item.source}</span><ArrowRight size={11} /><span>{item.target}</span></li>)}</ul>}</article>}
    </section>
  );
}
