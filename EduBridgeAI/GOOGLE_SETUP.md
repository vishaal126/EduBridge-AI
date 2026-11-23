# Setting up Google, GitHub and email for EduBridge sign-in

Nothing here is simulated: until these are configured, the Google / GitHub buttons and "Forgot password" show a
clear "not configured" message instead of pretending to work.

Put every value below in `.streamlit/secrets.toml` (copy `secrets.toml.example`) or in environment variables.
Never put a client secret in HTML or JavaScript.

## 0. Decide your app address (APP_BASE_URL)

Set `APP_BASE_URL` to the exact address people use to open the app, **without a trailing slash**:

* Local: `http://localhost:8501`
* Deployed: `https://your-app.example.com`

The OAuth redirect URI is this same address. The page must be opened at exactly this host: `localhost` and
`127.0.0.1` are different origins to Google.

## 1. Google sign-in (OpenID Connect)

1. Go to <https://console.cloud.google.com/> and sign in.
2. Top bar, project picker, **New project**. Name it `EduBridge AI`, **Create**, then select it.
3. **APIs & Services > Library**. Google sign-in uses the standard OpenID Connect endpoints, so no product API has to be
   enabled for basic sign-in. (If you are ever asked, the optional "Google People API" is not required here.)
4. **APIs & Services > OAuth consent screen** (newer consoles call this **Google Auth Platform > Branding / Audience**).
   * User type: **External**, **Create**.
   * App name `EduBridge AI`, a support email, a developer contact email, **Save**.
   * Scopes: add `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` (these are non-sensitive; no
     verification review needed).
   * Audience / Test users: while the app is in **Testing**, only the Google accounts you list under **Test users**
     can sign in. Add yourself. Click **Publish app** when you want everyone to be able to sign in.
5. **APIs & Services > Credentials > Create credentials > OAuth client ID**.
   * Application type: **Web application**, name `EduBridge web`.
   * **Authorized redirect URIs > Add URI**: your `APP_BASE_URL`, e.g. `http://localhost:8501`
     (add the production URL as a second entry when you deploy). It must match exactly: scheme, host, port, no trailing slash.
   * **Create**. Copy the **Client ID** and **Client secret**.
6. Put them in `secrets.toml`:
   ```toml
   APP_BASE_URL = "http://localhost:8501"
   GOOGLE_CLIENT_ID = "1234567890-abc.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET = "GOCSPX-..."
   ```
7. Restart Streamlit and click **Google** on the sign-in screen.

What the server does on the way back: validates the one-time `state`, binds it to the browser that started the flow,
exchanges the code (with PKCE) using the secret, then verifies the ID token's signature against Google's published keys,
issuer, audience, expiry, `nonce` and `email_verified` before looking at anything else.

Common errors: `redirect_uri_mismatch` = the URI in step 5 differs from `APP_BASE_URL`; `access_denied` / "app not verified"
= your account is not in Test users (step 4).

## 2. GitHub sign-in

1. GitHub > **Settings > Developer settings > OAuth Apps > New OAuth App**.
2. Application name `EduBridge AI`; Homepage URL = `APP_BASE_URL`;
   **Authorization callback URL** = `APP_BASE_URL` (e.g. `http://localhost:8501`). **Register application**.
3. **Generate a new client secret**; copy the Client ID and the secret.
4. ```toml
   GITHUB_CLIENT_ID = "Iv1...."
   GITHUB_CLIENT_SECRET = "..."
   ```
5. Restart. The app asks for `read:user user:email` and uses only a **verified** email from `/user/emails`
   (so private emails work). An account with no verified email is rejected with an explanation.

## 3. Password-reset email (SMTP)

Any SMTP service works. Example for Gmail (requires 2-step verification, then an App Password at
<https://myaccount.google.com/apppasswords>):

```toml
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USERNAME = "you@gmail.com"
SMTP_PASSWORD = "your-16-char-app-password"
SMTP_FROM = "EduBridge AI <you@gmail.com>"
```

Reset links look like `APP_BASE_URL/?reset=<token>`; they are single-use and expire in 30 minutes. Only a SHA-256 hash of
the token is stored. If sending fails, the user is told so (the app never claims an email went out when it did not).

## 4. HTTPS in production

Run behind HTTPS and set `APP_BASE_URL` to the `https://` address. The browser components then add the `Secure`
attribute to the session cookies automatically.
