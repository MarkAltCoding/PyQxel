// The kinds of account a portfolio may be, as the app names them, and which the IRS caps
// yearly contributions to. They mirror app/models/accounts.py.

import type { AccountType } from '../api/types'

export const ACCOUNT_TYPES: { value: AccountType; label: string }[] = [
  { value: 'taxable', label: 'Taxable brokerage account' },
  { value: 'traditional_ira', label: 'Traditional IRA' },
  { value: 'roth_ira', label: 'Roth IRA' },
  { value: 'rollover_ira', label: 'Rollover IRA' },
  { value: 'sep_ira', label: 'SEP IRA' },
  { value: 'simple_ira', label: 'SIMPLE IRA' },
  { value: 'traditional_401k', label: 'Traditional 401(k)' },
  { value: 'roth_401k', label: 'Roth 401(k)' },
  { value: '403b', label: '403(b)' },
  { value: '457b', label: '457(b)' },
  { value: 'solo_401k', label: 'Solo 401(k)' },
  { value: 'hsa', label: 'Health savings account (HSA)' },
  { value: '529', label: '529 education savings plan' },
  { value: 'other', label: 'Other account' },
]

/** Accounts the IRS caps yearly contributions to. */
export const CAPPED_ACCOUNT_TYPES: ReadonlySet<AccountType> = new Set<AccountType>([
  'traditional_ira',
  'roth_ira',
  'rollover_ira',
  'sep_ira',
  'simple_ira',
  'traditional_401k',
  'roth_401k',
  '403b',
  '457b',
  'solo_401k',
  'hsa',
])

export const accountTypeLabel = (type: AccountType | null | undefined): string | null =>
  ACCOUNT_TYPES.find((option) => option.value === type)?.label ?? null
