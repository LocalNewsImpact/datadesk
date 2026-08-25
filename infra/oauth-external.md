# Letting people outside the organisation sign in

Two separate walls stand between an invited colleague and this console,
and only one of them is Google's.

## 1. The application's own door

`ALLOWED_AUTH_DOMAINS` admits verified addresses in the consortium's
hosted domain. A personal Google account carries no `hd` claim at all,
so there is nothing for that check to accept — no consent screen
configuration changes it.

Invite them by address instead: **Users → Invited from outside**. An
invitation names one address, one dataset and one role (designer by
default), and the grant is made the first time they sign in. Withdrawing
the invitation closes the door; the role they hold is changed on Roles.

## 2. Google's consent screen

Configured at **Google Auth Platform** in the `lnic-datadesk` project.
User type **External**.

| Field | Value | Required |
|---|---|---|
| App name | Datadesk — Local News Impact Consortium | yes |
| User support email | data@localnewsimpact.org | yes |
| Application home page | `https://datadesk.localnewsimpact.org/` | yes |
| Privacy policy | `https://datadesk.localnewsimpact.org/privacy/` | yes |
| Terms of service | `https://datadesk.localnewsimpact.org/terms/` | recommended |
| Authorized domain | `localnewsimpact.org` | yes |
| Developer contact | data@localnewsimpact.org | yes |

Three constraints worth knowing, because they decided the pages above:

- **The home page and the policy must be reachable without signing in.**
  Every other page of this console is not. `/` is the sign-in page and is
  public, which is why it serves as the home page; `/privacy/` and
  `/terms/` are routed deliberately outside the wall, and a test keeps
  them there.
- **The home page must link the privacy policy.** The footer in
  `base.html` does that on every page.
- **Authorized domains must be verified in Google Search Console.** That
  is a domain-ownership step outside this repo. `localnewsimpact.org`
  covers every subdomain used here.

### Internal to External

The app is **Internal** today, which restricts it to members of the
Cloud Organization — the same wall as `ALLOWED_AUTH_DOMAINS`, one level
down, and the reason an invited outsider cannot reach the consent screen
at all.

**Switching to External is a console setting, not an approval.** It
widens who may authorize and makes the publishing status matter.

### Testing or In production

**No Google review is required either way for this app**, and the reason
is the scopes. Google's usual Testing penalties — 100 named test users,
an "unverified app" warning, and consent expiring after seven days
including refresh tokens — are waived for apps requesting only name,
email address and user profile. That is exactly what this asks for.

So:

| | Testing | In production |
|---|---|---|
| Who may sign in | 100 addresses, each added to the test-user list | anybody the app's own door admits |
| Warning screen | none, at these scopes | none |
| Consent expiry | none, at these scopes | none |
| Review | none | none, at these scopes |

Testing works, and costs a second list: every invited address has to be
added there as well as here, saying the same thing twice. Publishing
retires that list and leaves the invitation list as the only gate.

Verification is enforced for **sensitive or restricted scopes**, which
this app does not request. Brand verification is separate and is
triggered by External + Published **and wanting a logo or display name
on the consent screen** — publishing without a logo avoids it, uploading
one starts it.

## Once, to open the door at all

1. Google Auth Platform → **Audience** → change user type from Internal
   to **External**. No approval; the app drops to Testing.
2. Fill in the branding fields in the table above. **Do not upload a
   logo** unless a brand review is wanted.
3. **Publish app**, to retire the test-user list. At these scopes this
   needs no review.
4. Verify `localnewsimpact.org` in Google Search Console if it is not
   already, or the authorized domain will not accept those URLs.

## Adding somebody

1. Invite the address here: Users → Invited from outside, choosing the
   dataset and role.
2. If the app is still in Testing, add the same address as a test user
   in the Google console as well.
3. Send them `https://datadesk.localnewsimpact.org/` and have them sign
   in with the Google account for that address.

They hold designer on the dataset named, which reads and authors visuals
and decides no dispositions. Anything else is a role change on Roles.
