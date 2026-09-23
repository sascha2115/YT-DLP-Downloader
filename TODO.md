# TODO

## Planned site support

Future site additions, grouped by category:

### Video platforms
- [ ] Dailymotion
- [ ] Vimeo
- [ ] Bitchute
- [ ] PeerTube

### Social media
- [ ] Instagram
- [ ] Facebook
- [ ] Twitter (X)
- [ ] TikTok
- [ ] Reddit

## How to add a site

Each site becomes a profile in `SUPPORTED_SITES` (`ytdl/sites.py`): `domains`,
`id_regex`, `channel_url_regex` plus behavior flags (`sponsorblock`,
`js_runtime`, `resync_auto_subs`, `supports_subtitles`,
`always_extract_audio`, `info_timeout`, `slow_hint`). The strict domain gate
rejects anything without a profile, so the profile must land before the site
URL passes `url_rejection_reason()`.

Notes for the platforms above:
- Social-media sites are often login-walled — yt-dlp will likely need
  browser cookies (`--cookies-from-browser`) before they extract anything;
  expect `info_timeout` tuning and a `slow_hint` for slow upstream APIs.
- PeerTube is federated (instance domains, not one domain) — the `domains`
  list will need a pattern/instance strategy rather than fixed hostnames.
- Extend the URL-gate/ID tests in `temp/test_multisite_url.py` with each new
  site, and add an info-parse fixture suite like
  `temp/test_odysee_info_parse.py` once a real `-J` capture exists.
