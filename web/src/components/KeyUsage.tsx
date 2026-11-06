import type { ProviderUsage } from '../api/types'
import { dateTime } from '../lib/format'
import { amount, limitLabel, resetTime, shareLeft } from '../lib/usage'
import { InfoTip } from './InfoTip'

/** A key's plan usage: each limit, what is used and left, when it resets, and who counted. */
export function KeyUsage({
  report,
  onPlan,
}: {
  report: ProviderUsage
  /** Called with the plan the user says the key is on. */
  onPlan: (plan: string) => void
}) {
  const limits = report.limits ?? []
  const plans = report.plans ?? []
  return (
    <div className="stack key-usage" style={{ gap: 8 }}>
      <div className="filter-row" style={{ alignItems: 'center' }}>
        <div className="heading-with-info">
          <h4>Plan usage</h4>
          <InfoTip topic="planUsage" />
        </div>
        {plans.length > 1 ? (
          <label className="field inline">
            <span>Plan</span>
            <select value={report.plan ?? ''} onChange={(event) => onPlan(event.target.value)}>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name}
                  {plan.verified ? '' : ' (limits unconfirmed)'}
                </option>
              ))}
            </select>
          </label>
        ) : (
          report.plan_name && (
            <span className="muted">
              {report.plan_name}
              {report.plan_detected ? `, as ${report.name} reports` : ''}
            </span>
          )
        )}
      </div>
      {limits.length > 0 && (
        <div className="table-wrap">
          <table className="data usage-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="heading-with-info">
                    <span>Limit</span>
                    <InfoTip topic="usageLimit" />
                  </span>
                </th>
                <th scope="col" className="num">
                  <span className="heading-with-info">
                    <span>Left</span>
                    <InfoTip topic="usageLeft" />
                  </span>
                </th>
                <th scope="col" className="num">
                  <span className="heading-with-info">
                    <span>Used</span>
                    <InfoTip topic="usageUsed" />
                  </span>
                </th>
                <th scope="col">
                  <span className="heading-with-info">
                    <span>Resets</span>
                    <InfoTip topic="usageResets" />
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {limits.map((limit) => {
                const left = shareLeft(limit)
                return (
                  <tr key={`${limit.window}-${limit.unit}`}>
                    <td>
                      {limitLabel(limit)}
                      {limit.note && <div className="muted note">{limit.note}</div>}
                    </td>
                    <td
                      className={`num${left !== null && left < 0.1 ? ' down' : left !== null && left < 0.3 ? ' low' : ''}`}
                    >
                      {amount(limit, limit.remaining)}
                    </td>
                    <td className="num">
                      {amount(limit, limit.used)}
                      <div className="muted note">
                        {limit.source === 'provider' ? `per ${report.name}` : 'PyQxel’s count'}
                      </div>
                    </td>
                    <td className="resets">
                      {resetTime(limit)}
                      <div className="muted note">{limit.resets}</div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {report.note && <p className="disclaimer">{report.note}</p>}
      {report.reported_at && (
        <p className="disclaimer">
          {report.name} last reported figures at {dateTime(report.reported_at)}.
        </p>
      )}
    </div>
  )
}
