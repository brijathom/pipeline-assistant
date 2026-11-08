import type { InvestigationReport } from '../api/investigations'
import { SourceTime } from './SourceTime'

export function InvestigationReportContent({ report, generatedAt }: { report: InvestigationReport; generatedAt: string | null }) {
  return (
    <div className="space-y-4 text-xs leading-5">
      <p className="text-slate-700">{report.summary}</p>
      <p className="text-slate-500">Generated <SourceTime value={generatedAt} /></p>
      {report.findings.map((finding, index) => (
        <article key={index} className="rounded-lg border border-slate-200 p-4" aria-label={finding.title}>
          <h4 className="font-semibold text-slate-900">{finding.title}</h4>
          <p className="mt-2 text-slate-700">{finding.observation}</p>
          <p className="mt-3 text-slate-500">{finding.citation.job_name} · Source line {finding.citation.source_line}</p>
          <blockquote className="mt-2 whitespace-pre-wrap break-all rounded-md bg-slate-950 p-3 font-mono text-slate-100">{finding.citation.quote}</blockquote>
          <h5 className="mt-3 font-semibold text-slate-800">Suggested next step</h5>
          <p className="mt-1 text-slate-700">{finding.recommendation}</p>
        </article>
      ))}
      <div>
        <h4 className="font-semibold text-slate-800">Limitations</h4>
        <ul className="mt-2 list-disc space-y-1 pl-4 text-slate-500">{report.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul>
      </div>
    </div>
  )
}
