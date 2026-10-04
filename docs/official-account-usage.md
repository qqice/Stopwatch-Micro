# Official account usage capability audit

Verified 2026-10-04 against the existing Mac standalone CLI0.159.0, Windows
desktop-managed CLI0.160.0, their generated experimental JSON schemas and a
read-only authenticated account response. No device token logs, conversation
content, reset actions or private account identifiers were collected.

## Account-wide, not device-local

The documented [`account/usage/read`](https://learn.chatgpt.com/docs/app-server)
returns account token activity when no `threadId` is supplied. The actual response
contained 120 daily buckets, each with only `startDate` and `tokens`, and five
summary metrics: lifetime tokens, peak daily tokens, longest-running turn seconds,
current streak days and longest streak days. Null remains unknown. The monitor's
existing official daily history uses this account-wide response, not the Mac's
or Windows computer's local session logs. It should not be described as ordinary
ChatGPT web-chat activity or API Platform organization billing.

`account/rateLimits/read` separately returns current metered buckets and their
window usage percentage, length, reset time, plan/credits when provided, and
earned-reset count. This account currently returns one canonical Codex weekly
window, not a guaranteed five-hour or per-model window. Bucket identity and
duration are read dynamically; no hard-coded plan-to-window inference is used.

## The newer schema does contain model information, but at thread scope

The locally generated0.159/0.160 protocol exposes an optional `threadId` parameter
for `account/usage/read`. That requests **estimated usage for a particular
thread**, not an account-wide daily model report. Its `threadUsage.groups` schema
can include model, reasoning effort, speed, input/cached/net-new input/output/total
tokens and estimated credit micros. This explains why a no-parameter response
with `threadUsage:null` does not prove the protocol has no model information.

However, those same schemas provide no date range, model/group-by parameter or
account-daily-model buckets. The account response actually returned no model
dimension. `thread/list` is a list of locally persisted sessions, not evidence of
complete enumeration across every device. Combining known local threads would
therefore not fulfill a whole-account requirement, and thread total estimates
cannot be assigned to a particular consumption day merely by creation/update time.

The Desktop product can expose richer usage-insights UI; this does not make every
internal route a documented standalone personal API. The published
[workspace Analytics API](https://learn.chatgpt.com/docs/enterprise/analytics-api)
has its own administrator/workspace permission boundary, not this personal Pro
account's automatic access. No private endpoint has been substituted or claimed
to be a stable official export interface.

## What must not be inferred

There is currently no verified public account-wide export in this setup for
“daily GPT-6.1-sol allowance percentage.” Model token/credit composition, estimated
thread cost and a quota-window `usedPercent` are different quantities. Do not
convert tokens or credits to subscription percentage or infer a per-model quota.
Any future implementation must first obtain an authoritative account-wide source
and preserve its scope, freshness, unknowns and actual denominator.

The device continues to display official account daily totals and observation-time
hourly increments (which can lag and are not actual consumption hours). The
existing service remains unchanged by this audit; no new local uploader or
partial-coverage model aggregation was installed.

After the owner upgraded the Mac standalone CLI to0.160.0, the same authenticated
read was repeated: account usage still had the five summary fields,121 daily
buckets containing only dates/tokens, and `threadUsage:null` without a threadId.
Rate limits still returned one primary bucket, with no daily-model breakdown.
The already generated0.160 protocol therefore did not reveal a new account-wide
model/day query; the desktop-bundled Mac CLI remained0.159.2. This does not deny
richer Desktop views, only the verified standalone export contract.
