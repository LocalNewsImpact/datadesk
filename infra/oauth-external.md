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

### Testing or In production

**Testing** admits up to 100 named test users without review, and each
one has to be added to the test-user list as well as invited here — two
lists saying the same thing.

**In production** admits anybody the application's own door lets in,
which is the list above. This app requests `profile` and `email` only,
which Google classes as non-sensitive, so publishing needs no security
assessment.

Brand verification is triggered by External + Published **and wanting a
logo or display name on the consent screen**. Publishing without a logo
avoids the review; uploading one starts it.

## Adding somebody

1. Invite the address here: Users → Invited from outside, choosing the
   dataset and role.
2. If the OAuth app is still in Testing, add the same address as a test
   user in the Google console.
3. Send them `https://datadesk.localnewsimpact.org/` and have them sign
   in with the Google account for that address.

They hold designer on the dataset named, which reads and authors visuals
and decides no dispositions. Anything else is a role change on Roles.
