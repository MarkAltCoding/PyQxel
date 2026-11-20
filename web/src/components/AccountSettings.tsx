import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getContributionLimits, saveAccountSettings } from '../api/endpoints'
import type { AccountType, ContributionLimits, PortfolioSummary } from '../api/types'
import { ACCOUNT_TYPES, CAPPED_ACCOUNT_TYPES } from '../lib/accounts'
import { money, shortDate } from '../lib/format'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'

/**
 * What kind of account a saved portfolio is, such as a Roth IRA, and for one the IRS caps,
 * how much more may go into it this year. Saved with the portfolio, so every review of it
 * knows both.
 */
export function AccountSettingsFields({ summary }: { summary: PortfolioSummary }) {
  const queryClient = useQueryClient()
  const [room, setRoom] = useState<string>(
    summary.contribution_room == null ? '' : String(summary.contribution_room),
  )
  const save = useMutation({
    mutationFn: (next: { account_type: AccountType | null; contribution_room: number | null }) =>
      saveAccountSettings(summary.portfolio, next),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['portfolios'] }),
  })
  const type = summary.account_type ?? null
  const capped = type !== null && CAPPED_ACCOUNT_TYPES.has(type)
  const roomValue = room.trim() === '' ? null : Number(room)
  const roomInvalid = roomValue !== null && (!Number.isFinite(roomValue) || roomValue < 0)
  return (
    <div className="stack" style={{ gap: 8, marginTop: 12 }}>
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <div className="field">
          <span className="heading-with-info">
            <span id={`account-type-${summary.portfolio}`}>Account type</span>
            <InfoTip topic="accountType" />
          </span>
          <select
            aria-labelledby={`account-type-${summary.portfolio}`}
            value={type ?? ''}
            disabled={save.isPending}
            onChange={(event) =>
              save.mutate({
                account_type: (event.target.value || null) as AccountType | null,
                contribution_room: roomValue,
              })
            }
          >
            <option value="">Not set</option>
            {ACCOUNT_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {capped && (
          <div className="field" style={{ width: 220 }}>
            <span className="heading-with-info">
              Room left to contribute this year
              <InfoTip topic="contributionRoom" />
            </span>
            <span className="money-input">
              <span aria-hidden="true">$</span>
              <input
                type="number"
                min={0}
                step="any"
                aria-label="Room left to contribute this year, in dollars"
                aria-invalid={roomInvalid}
                value={room}
                onChange={(event) => setRoom(event.target.value)}
                onBlur={() => {
                  if (!roomInvalid && roomValue !== (summary.contribution_room ?? null)) {
                    save.mutate({ account_type: type, contribution_room: roomValue })
                  }
                }}
              />
            </span>
          </div>
        )}
        {save.isPending && <span className="muted">Saving…</span>}
      </div>
      {save.error !== null && <ErrorNotice error={save.error} />}
      {capped && <ContributionLimitsNote />}
    </div>
  )
}

/** The IRS's yearly limits, how current they are, and how they usually change. */
export function ContributionLimitsNote() {
  const limits = useQuery({
    queryKey: ['contribution-limits'],
    queryFn: getContributionLimits,
    staleTime: Infinity,
  })
  if (limits.error !== null) return <ErrorNotice error={limits.error} />
  if (!limits.data) return null
  return (
    <details className="contribution-limits">
      <summary>Yearly contribution limits ({limits.data.current.year})</summary>
      <LimitsBody limits={limits.data} />
    </details>
  )
}

function LimitsBody({ limits }: { limits: ContributionLimits }) {
  const years = [limits.current, ...(limits.next ? [limits.next] : [])]
  return (
    <div className="stack" style={{ gap: 10, marginTop: 10 }}>
      <Notice tone="warning">
        {limits.outdated
          ? `These are the ${limits.current.year} limits, the newest PyQxel has, and are likely out of date: check irs.gov before contributing.`
          : `These limits are for the ${years.map((y) => y.year).join(' and ')} tax year${years.length > 1 ? 's' : ''}, from the IRS, as of ${shortDate(limits.as_of)}. No data source PyQxel uses publishes them automatically, so they are updated by hand each year and may lag a new announcement.`}
      </Notice>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Account</th>
              {years.map((year) => (
                <th key={year.year} className="num">
                  {year.year} limit
                </th>
              ))}
              <th className="num">Age 50+ adds</th>
              <th className="num">Ages 60–63 add</th>
            </tr>
          </thead>
          <tbody>
            {limits.current.limits.map((limit, index) => (
              <tr key={limit.account}>
                <td className="wrap">
                  {limit.account}
                  {limit.note && <div className="muted">{limit.note}</div>}
                </td>
                {years.map((year) => (
                  <td key={year.year} className="num">
                    {money(year.limits[index]?.limit, 'USD', 0)}
                  </td>
                ))}
                <td className="num">
                  {limit.catch_up_50 == null ? '—' : money(limit.catch_up_50, 'USD', 0)}
                </td>
                <td className="num">
                  {limit.catch_up_60_63 == null ? '—' : money(limit.catch_up_60_63, 'USD', 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        A Roth IRA contribution shrinks for incomes between{' '}
        {years
          .map(
            (year) =>
              `${money(year.roth_ira_phase_out_single[0], 'USD', 0)} and ${money(year.roth_ira_phase_out_single[1], 'USD', 0)} for a single filer (${money(year.roth_ira_phase_out_joint[0], 'USD', 0)} and ${money(year.roth_ira_phase_out_joint[1], 'USD', 0)} filing jointly) in ${year.year}`,
          )
          .join('; ')}
        , and is not allowed above them.
      </p>
      <ul className="muted">
        {limits.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
      <p className="disclaimer">
        Source:{' '}
        <a href={limits.current.source_url} target="_blank" rel="noreferrer">
          {limits.current.source}
        </a>
        {limits.next && (
          <>
            {'; '}
            <a href={limits.next.source_url} target="_blank" rel="noreferrer">
              {limits.next.source}
            </a>
          </>
        )}
        .
      </p>
    </div>
  )
}
