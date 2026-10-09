"""The privacy notice served at GET /privacy (linked from the app and the Play listing).

Written for India's Digital Personal Data Protection Act 2023 and Rules 2025: plain language,
itemised data and purposes, how to withdraw consent, exercise rights and complain. Have it
reviewed by a lawyer before launch; edit the wording here, and bump PRIVACY_VERSION in config
when it changes so the app asks customers to agree again.
"""
from html import escape

import config


def notice_html() -> str:
    company = escape(config.PRIVACY_COMPANY)
    email = escape(config.PRIVACY_CONTACT_EMAIL or "(contact email not set)")
    days = config.RETENTION_DAYS
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy notice · {company}</title>
<style>
 body{{font:16px/1.6 system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px 16px;color:#1b2330;background:#fff}}
 h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:28px 0 6px}}
 .muted{{color:#5f6b7a}} li{{margin:4px 0}} a{{color:#1565c0}}
 @media (prefers-color-scheme:dark){{body{{background:#12161c;color:#e6e9ee}} .muted{{color:#9aa4b2}} a{{color:#7fb3ff}}}}
</style></head><body>
<h1>Privacy notice</h1>
<p class="muted">{company} · version {escape(config.PRIVACY_VERSION)}</p>

<p>This app turns photos of an object into a 3D model. This notice explains what personal data
we use, why, and what you can do about it.</p>

<h2>What we collect</h2>
<ul>
 <li><b>Photos you take in the app</b> and the <b>3D models</b> we make from them.</li>
 <li><b>An account ID and a secret key</b> created on your phone when you first open the app.
     We don't ask for your name, email or phone number.</li>
 <li><b>Points records</b>: purchases (the Google Play order reference, amount and date) and
     each scan you spend points on.</li>
 <li><b>Technical logs</b>: IP address and the requests your phone makes, used to keep the
     service secure and working.</li>
</ul>
<p>Please photograph objects only. Don't scan people, faces, ID cards or private documents.</p>

<h2>Why we use it</h2>
<ul>
 <li>To build your 3D model and let you preview and download it.</li>
 <li>To keep your points balance and history, and refund points when a scan fails.</li>
 <li>To prevent fraud and abuse, and to meet tax and accounting law.</li>
</ul>
<p>We use your data only for these purposes, with your consent, which you give in the app
before your first scan. We don't sell it or use it for advertising.</p>

<h2>Who helps us</h2>
<ul>
 <li><b>3D processing services</b> receive your photos to build the model.</li>
 <li><b>Cloud hosting and storage providers</b> run our server and store files.</li>
 <li><b>Google Play</b> handles payments. We never see your card or bank details.</li>
</ul>
<p>These providers may process data outside India, including in Singapore and the United States.</p>

<h2>How long we keep it</h2>
<ul>
 <li>Photos and 3D models: deleted automatically <b>{days} days</b> after the scan, or straight
     away when you delete your data.</li>
 <li>Points and purchase records: kept for as long as tax and accounting law requires, then deleted.</li>
 <li>Backup copies of our database: deleted within 30 days.</li>
</ul>

<h2>Your rights</h2>
<ul>
 <li><b>See your data</b>: your points history is in the app under Wallet. Email us for a copy
     of everything we hold about your account.</li>
 <li><b>Correct it</b>: email us.</li>
 <li><b>Withdraw consent and delete your data</b>: see below.</li>
 <li><b>Nominate</b> someone to act for you if you die or can't act yourself: email us.</li>
 <li><b>Complain</b>: email us first. If you're not satisfied with our answer, you can
     complain to the Data Protection Board of India.</li>
</ul>

<h2 id="delete">Delete your data</h2>
<p>In the app, open <b>Wallet → Delete my data</b>. This deletes your photos and models and
closes your account at once. Any points left are forfeited, as points can't be refunded to cash.
We keep only the payment records the law requires.</p>
<p>If you no longer have the app, email us your account ID (shown in Wallet) and we'll delete it
for you. Uninstalling the app alone doesn't delete data on our servers, but your photos and
models are still deleted after {days} days.</p>

<h2>Children</h2>
<p>This app is not for anyone under 18.</p>

<h2>Security</h2>
<p>Data travels encrypted (HTTPS). Upload and download links expire within an hour. If a
security breach affects your data, we will tell you and the Data Protection Board.</p>

<h2>Contact</h2>
<p>Privacy questions, requests and complaints: <a href="mailto:{email}">{email}</a></p>
</body></html>"""
