import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { apiUrl } from '../api/client'
import { buyCredit, getBilling, openBillingPortal, startSubscription } from '../api/endpoints'
import type { BillingEntry, BillingInterval, BillingStatus } from '../api/types'
import {
  BILLING_QUERY_KEY,
  DEFAULT_TOP_UP_CENTS,
  INTERVAL_CHOICES,
  INTERVAL_NAMES,
  TOP_UP_PRESETS,
  creditDays,
  checkoutCents,
  paymentPlace,
  periodBills,
} from '../lib/billing'
import { dateTime, money, percent } from '../lib/format'
import { InfoTip } from './InfoTip'
import { ContactLink } from './LegalSection'
import { ErrorNotice, Notice } from './Notice'
import { PresetOrCustom } from './PresetOrCustom'
import { Segmented } from './Segmented'
import { Stat } from './Stat'

/** How long Settings keeps checking for a payment Stripe has not reported yet. */
const AWAIT_PAYMENT_MS = 60_000

/** Money with tenths of a cent when it is under a dollar, since a night's refresh can
 * cost less than a cent. */
/** A country's English name from its ISO code, or the code if the browser has no name. */
function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(['en-US'], { type: 'region' }).of(code) ?? code
  } catch {
    return code
  }
}

function smallMoney(dollars: number): string {
  return money(dollars, 'USD', Math.abs(dollars) > 0 && Math.abs(dollars) < 1 ? 3 : 2)
}

const ENTRY_NAMES: Record<BillingEntry['kind'], string> = {
  top_up: 'Credit added',
  usage: 'Nightly refresh',
  fee: 'Stripe fee',
  refund: 'Refund',
  dispute: 'Disputed payment',
}

/** Paying for the nightly refresh: prepaid credit, a usage subscription, and the history
 * of both. Payments happen on Stripe's own pages; in the native app, on the website. */
export function BillingCard() {
  const [params, setParams] = useSearchParams()
  const returned = params.get('billing')
  const [awaiting, setAwaiting] = useState(returned !== null)
  const status = useQuery({
    queryKey: BILLING_QUERY_KEY,
    queryFn: getBilling,
    refetchInterval: awaiting ? 3000 : false,
  })

  useEffect(() => {
    if (!awaiting) return
    const timer = setTimeout(() => setAwaiting(false), AWAIT_PAYMENT_MS)
    return () => clearTimeout(timer)
  }, [awaiting])

  const data = status.data
  const place = paymentPlace()
  if (status.error) {
    return (
      <section id="billing" className="card stack" style={{ gap: 14 }}>
        <h2>Billing</h2>
        <ErrorNotice error={status.error} />
      </section>
    )
  }
  if (!data || !data.enabled) return null

  const clearReturn = () => {
    params.delete('billing')
    setParams(params, { replace: true })
  }

  return (
    <section id="billing" className="card stack" style={{ gap: 14 }}>
      <div>
        <div className="heading-with-info">
          <h2>Billing</h2>
          <InfoTip topic="billing" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Using PyQxel is free. Only the nightly screener refresh costs money: it runs on PyQxel’s
          cloud server for minutes or hours each night. You pay what your refreshes cost that
          server, with nothing added, from prepaid credit or a subscription that bills what they
          cost each week, month, or year. Payments go through Stripe, whose processing fee on each
          payment you also pay, at cost; PyQxel never sees your card. Payments from the European
          Union and the United Kingdom are not accepted, since PyQxel is not registered to collect
          their VAT.
        </p>
      </div>

      {data.closed_country && (
        <Notice tone="warning">
          Your payment from {countryName(data.closed_country)} was turned away: the hold on your
          card was released, so nothing was charged. PyQxel cannot take payments from the European
          Union or the United Kingdom, since it is not registered to collect their VAT. If you live
          elsewhere, email <ContactLink />.
        </Notice>
      )}
      {returned === 'paid' && !data.closed_country && (
        <Notice>
          Thank you. Your credit appears here within a few seconds of Stripe confirming the payment.{' '}
          <button type="button" className="link-button" onClick={clearReturn}>
            Dismiss
          </button>
        </Notice>
      )}
      {returned === 'subscribed' && (
        <Notice>
          Your subscription starts as soon as Stripe confirms it, usually within seconds.{' '}
          <button type="button" className="link-button" onClick={clearReturn}>
            Dismiss
          </button>
        </Notice>
      )}
      {data.exempt ? (
        <Notice>Your account’s refreshes are not billed.</Notice>
      ) : (
        !data.can_refresh && (
          <Notice tone="warning">
            Your nightly refresh is paused until you add credit or start a subscription below.
          </Notice>
        )
      )}

      <BillingStats status={data} />

      {place === 'website' && (
        <Notice>
          Add credit or manage your subscription on the{' '}
          <a href={apiUrl('/settings#billing')} target="_blank" rel="noopener noreferrer">
            PyQxel website
          </a>
          , signed in with the same account.
        </Notice>
      )}
      {place === 'here' && !data.closed_country && (
        <>
          <TopUp status={data} />
          <SubscriptionChoice status={data} />
          <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
            Paying means you agree to the <Link to="/terms">terms of service</Link>; unused credit
            is refundable, as the <Link to="/refunds">refund policy</Link> explains.
          </p>
        </>
      )}

      <History entries={data.entries} />
    </section>
  )
}

function BillingStats({ status }: { status: BillingStatus }) {
  const days = creditDays(status)
  const subscription = status.subscription
  return (
    <div className="stats">
      <Stat
        info="billingCredit"
        label="Credit"
        value={smallMoney(status.credit_usd)}
        detail={
          status.credit_usd < 0
            ? 'Owed; your next top-up repays it'
            : days !== null
              ? `About ${Math.floor(days).toLocaleString()} days at your recent pace`
              : undefined
        }
      />
      <Stat
        info="billingLast30Days"
        label="Last 30 days"
        value={smallMoney(status.last_30_days_usd)}
        detail={`${status.last_30_days_runs.toLocaleString()} refreshes`}
      />
      <Stat
        info="billingSubscription"
        label="Subscription"
        value={subscription ? `Billed each ${INTERVAL_NAMES[subscription.interval]}` : 'None'}
        detail={
          subscription
            ? subscription.status === 'past_due'
              ? 'A payment failed; Stripe is retrying it'
              : subscription.cancels
                ? `Ends ${dateTime(subscription.renews_at)}`
                : subscription.renews_at
                  ? `Next bill ${dateTime(subscription.renews_at)}`
                  : undefined
            : undefined
        }
      />
    </div>
  )
}

function TopUp({ status }: { status: BillingStatus }) {
  const [cents, setCents] = useState(DEFAULT_TOP_UP_CENTS)
  const charged = checkoutCents(cents, status)
  const fee = charged - cents
  const buy = useMutation({
    mutationFn: () => buyCredit(cents),
    onSuccess: ({ url }) => window.location.assign(url),
  })
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="heading-with-info">
        <h3>Add credit</h3>
        <InfoTip topic="billingTopUp" />
      </div>
      <PresetOrCustom
        label="Credit to add"
        presets={TOP_UP_PRESETS}
        value={cents}
        onChange={setCents}
        min={status.min_top_up_cents / 100}
        max={status.max_top_up_cents / 100}
        step={1}
        unit="dollars"
        scale={100}
      />
      <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
        Any amount from {money(status.min_top_up_cents / 100, 'USD', 0)} to{' '}
        {money(status.max_top_up_cents / 100, 'USD', 0)}. You pay Stripe’s processing fee on top:{' '}
        {money(fee / 100)} on this payment, {percent(fee / charged)} of it, so a larger top-up pays
        less in fees for the same credit. A card issued outside the US costs Stripe{' '}
        {percent(status.international_fee_rate)} more, which is taken from the credit. Sales tax or
        VAT is added where PyQxel collects it, for the billing address you give Stripe.
      </p>
      <div>
        <button
          type="button"
          className="button primary"
          onClick={() => buy.mutate()}
          disabled={buy.isPending}
        >
          {buy.isPending
            ? 'Opening Stripe…'
            : `Pay ${money(charged / 100)} for ${money(cents / 100)} of credit`}
        </button>
      </div>
      {buy.error && <ErrorNotice error={buy.error} />}
    </div>
  )
}

function SubscriptionChoice({ status }: { status: BillingStatus }) {
  const [interval, setInterval] = useState<BillingInterval>('month')
  const subscribe = useMutation({
    mutationFn: () => startSubscription(interval),
    onSuccess: ({ url }) => window.location.assign(url),
  })
  const portal = useMutation({
    mutationFn: openBillingPortal,
    onSuccess: ({ url }) => window.location.assign(url),
  })
  const bills = periodBills(status)
  const subscribed = status.subscription !== null && status.subscription !== undefined
  const paidBefore = status.entries.length > 0 || subscribed

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="heading-with-info">
        <h3>Subscription</h3>
        <InfoTip topic="billingSubscription" />
      </div>
      <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
        Instead of topping up, let Stripe bill what your refreshes cost at the end of each week,
        month, or year, after any credit you have is used. Each bill includes Stripe’s fees on it,{' '}
        {status.card_fee_cents} cents plus {percent(status.card_fee_rate + status.billing_fee_rate)}
        , so billing less often pays less in fees. Stripe charges nothing under{' '}
        {money(status.min_charge_cents / 100)}: a smaller bill waits until the next one. Sales tax
        or VAT is added where PyQxel collects it.
      </p>
      {bills.length > 0 ? (
        <ul className="stack" style={{ gap: 4, margin: 0, paddingLeft: 18 }}>
          {bills.map((bill) => (
            <li key={bill.interval}>
              {INTERVAL_CHOICES.find((choice) => choice.value === bill.interval)?.label}: about{' '}
              {smallMoney(bill.usageCents / 100)} of refreshes each {INTERVAL_NAMES[bill.interval]},
              with fees adding {percent(bill.feeShare)} to it.
            </li>
          ))}
        </ul>
      ) : (
        <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
          Once your refreshes have run, this shows what each choice would add in fees at your pace.
        </p>
      )}
      {subscribed ? (
        <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
          Change your card, see your invoices, or cancel your subscription on Stripe’s billing page.
        </p>
      ) : (
        <div className="filter-row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Segmented
            label="How often to bill"
            value={interval}
            options={INTERVAL_CHOICES}
            onChange={setInterval}
          />
          <button
            type="button"
            className="button"
            onClick={() => subscribe.mutate()}
            disabled={subscribe.isPending}
          >
            {subscribe.isPending ? 'Opening Stripe…' : 'Subscribe with Stripe'}
          </button>
        </div>
      )}
      {paidBefore && (
        <div>
          <button
            type="button"
            className="button ghost"
            onClick={() => portal.mutate()}
            disabled={portal.isPending}
          >
            {portal.isPending ? 'Opening Stripe…' : 'Manage billing'}
          </button>
        </div>
      )}
      {subscribe.error && <ErrorNotice error={subscribe.error} />}
      {portal.error && <ErrorNotice error={portal.error} />}
    </div>
  )
}

const HISTORY_SHOWN = 10

function History({ entries }: { entries: BillingEntry[] }) {
  if (entries.length === 0) return null
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="heading-with-info">
        <h3>History</h3>
        <InfoTip topic="billingHistory" />
      </div>
      <ul className="billing-history">
        {entries.slice(0, HISTORY_SHOWN).map((entry, index) => (
          <li key={`${entry.created_at}-${index}`}>
            <span>
              {ENTRY_NAMES[entry.kind]}
              <span style={{ color: 'var(--text-secondary)' }}>
                {' '}
                · {dateTime(entry.created_at)}
              </span>
            </span>
            <span>
              {entry.credit_usd !== 0 &&
                `${entry.credit_usd > 0 ? '+' : ''}${smallMoney(entry.credit_usd)} credit`}
              {entry.credit_usd !== 0 && entry.subscription_usd !== 0 && ', '}
              {entry.subscription_usd !== 0 && `${smallMoney(entry.subscription_usd)} billed`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
