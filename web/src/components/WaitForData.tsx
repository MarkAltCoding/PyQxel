import type { WaitPlan, WaitPlans } from '../api/types'
import { WAIT_CHOICES } from '../lib/waitForData'
import { InfoTip } from './InfoTip'

/** How long to wait for data a provider's per-minute limit holds back, with what each
 * choice would gather, when the page has worked it out. */
export function WaitForDataOption({
  seconds,
  onChange,
  disabled = false,
  plans,
}: {
  seconds: number
  onChange: (seconds: number) => void
  disabled?: boolean
  /** What each wait would gather for this request, from the server. */
  plans?: WaitPlans
}) {
  return (
    <span className="heading-with-info wait-for-data-option">
      <label className="field inline">
        <span>Wait for more data</span>
        <select
          value={seconds}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        >
          {WAIT_CHOICES.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      <InfoTip topic="waitForData">
        {plans && <WaitPlanTable plans={plans} chosen={seconds} />}
      </InfoTip>
    </span>
  )
}

function label(seconds: number): string {
  return WAIT_CHOICES.find((choice) => choice.value === seconds)?.label ?? `${seconds} seconds`
}

/** "12 of 30" when some are left out, else "all 30". */
function share(part: number, whole: number): string {
  if (whole === 0) return '–'
  return part >= whole ? `all ${whole}` : `${part} of ${whole}`
}

function groupLine(plan: WaitPlan): string {
  return plan.groups
    .map(
      (group) =>
        `${group.name}: news for ${share(group.with_news, group.stocks)}` +
        (group.with_news
          ? ` (up to ${group.articles_each} full article${group.articles_each === 1 ? '' : 's'} and ${group.headlines_each} headlines each)`
          : '') +
        `, ratings and earnings for ${share(group.with_signals, group.stocks)}`,
    )
    .join('; ')
}

/** Each wait's figures: what the time buys, the likeliest picks or largest holdings first. */
function WaitPlanTable({ plans, chosen }: { plans: WaitPlans; chosen: number }) {
  return (
    <div className="wait-plans">
      <p>
        {plans.per_minute
          ? `Your Finnhub plan answers ${plans.per_minute} requests a minute. What each choice gathers, in order of priority:`
          : 'Without a Finnhub key, news comes from Alpha Vantage’s shared feed and waiting adds nothing. What is gathered:'}
      </p>
      <ul>
        {plans.choices.map((plan) => (
          <li key={plan.wait_seconds} className={plan.wait_seconds === chosen ? 'chosen' : ''}>
            <strong>{label(plan.wait_seconds)}</strong>
            {plan.complete ? ' (everything)' : ''}: {groupLine(plan)}. In all, up to {plan.articles}{' '}
            full company articles and {plan.headlines} company headlines, plus{' '}
            {plan.market_articles} market and deal stories in full and {plan.market_headlines}{' '}
            market headlines; {plan.calendar_days} of {plan.full_calendar_days} days of earnings
            calendar.
          </li>
        ))}
      </ul>
    </div>
  )
}
