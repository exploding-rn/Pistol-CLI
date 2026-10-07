# Global configuration and LECAP

`pistol config` controls global Pistol settings. `pistol chamber` owns runtime and project association. `pistol lecap` (Local Environment Chamber Access Point) owns access for the current chamber. All operations run locally, without accounts or hosted services.

## Global configuration

```powershell
pistol config
pistol config --chamberlain pistolchamber --host Keller --https true
pistol config --json
```

Names are normalized to lowercase DNS names (including IDNA for international names). `host` and `chamberlain` each represent one DNS label. Boolean inputs and JSON settings use `true` and `false` exclusively. Defaults are `pistolchamber`, the normalized machine name, and HTTPS `false`.

Global settings extend the existing `%APPDATA%\Pistol\config.json`; unrelated settings, including `active_chamber`, are preserved. On other platforms Pistol uses its existing config/data directory conventions. Enabling global HTTPS prepares one global CA. On Windows, the first interactive `pistol config --https true` offers to enroll its public certificate in the current user's Root store; only an explicit `y` or `yes` does so. Declining, pressing Enter, running without an interactive terminal, or using `--json` leaves HTTPS enabled and reports `pistol config --trust-ca` for later. Repeating `--https true` does not prompt. Configuration changes never edit DNS, start a listener, or change the firewall.

## Direct access and inheritance

Inside a chamber project, `pistol lecap` shows effective configuration and sources (`local`, `global`, `chamber/default`). Descendant folders also resolve to their owning chamber. If multiple chambers share the project, select one with `pistol chamber --switch NAME`.

Without identity overrides or `--proxy true`, a chamber on port 5000 uses direct access such as `http://pistolchamber.keller:5000`. Changing only `--port` retains direct mode. No custom LECAP or proxy process is required. The application must actually listen on that port; Pistol cannot turn the application's HTTP listener into HTTPS. Use `--proxy true` for managed HTTPS with the inherited identity. Two portless routes cannot claim the same hostname; direct routes can share a hostname because their ports distinguish them.

Local settings live in `%LOCALAPPDATA%\Pistol\chambers\NAME\lecap.json`, separate from `chamber.json`, with a versioned `overrides` object. Nothing is written into the project. Changes to inherited global settings or the chamber's port are reflected on the next read.

## Configure and run a proxy

```powershell
cd C:\projects\LAICK-API-MAIN
pistol lecap --port 5000 --chamberlain laick --subdomain ai
pistol lecap --dns plan
pistol lecap --serve
```

With global `host=keller` and `https=true`, this gives `https://ai.laick.keller/v1`, routed to `http://127.0.0.1:5000/v1`. Port 5000 is always an internal target. Pistol uses ordinary ports 80 and 443 for proxy traffic. Configure and start your chamber application separately with `pistol fire`; LECAP does not alter the runtime entrypoint or start the application.

`--serve` toggles one detached background proxy for **all enabled custom routes** and returns control to the terminal. Run `pistol fire` in that same terminal afterward; stopping the application with Ctrl+C does not stop LECAP. Use explicit `--serve start` and `--serve stop` in scripts: starting an existing worker or stopping an absent one is a successful no-op. All forms support `--json`.

The proxy binds loopback by default. Routes and global settings are checked once per second; a changed, disabled, reset or deleted route stops the proxy, so stale access does not persist. Status reports that a restart is required; run `--serve start` to load the new configuration. Certificates are checked hourly while serving and renewed as needed. Pistol never starts a second worker while a verified instance or its lifetime lock exists.

On Windows the internal `python -I -m pistol.lecap_worker` runs with `CREATE_NO_WINDOW`, with standard streams redirected to the null device and no inherited console handles. This also keeps the virtual-environment redirector's child interpreter windowless. `DETACHED_PROCESS` is deliberately omitted: the redirector does not forward that flag, and combining it with `CREATE_NO_WINDOW` would cause Windows to ignore the latter. The worker is independent of the launching command and later foreground applications' console events; no additional process-group or window-hiding flags are needed. Diagnostics use the worker's rotating file logger. There is no Windows Service, login/startup registration or reboot persistence.

Runtime files live in `%LOCALAPPDATA%\Pistol\runtime\lecap`. They contain the worker PID, creation time, executable, exact worker arguments and a unique nonsecret instance ID, plus public route fingerprints, URLs and listener addresses. Before stopping, Pistol verifies these against the live OS process. It requests graceful shutdown through an instance-specific control record, waits for exit and never kills a PID merely because a file contains it. Stale/reused PIDs are discarded without signaling the unrelated process; an identity that cannot be verified due to permissions fails safely. The worker closes its listeners before recording shutdown. A lifetime lock also prevents duplicate workers if state is removed.

Startup is acknowledged only after the worker reports readiness and the launcher checks its process identity, owned listeners and HTTP route health. The worker exits if the launcher disappears before acknowledging startup. Bind conflicts report the owning PID/name when available; no conflicting process is stopped. Background diagnostics rotate at `%LOCALAPPDATA%\Pistol\logs\lecap.log` (1 MB, three backups), without request URLs, environment values or key material. `pistol lecap` shows the verified PID, and JSON includes `proxy_runtime` with `active`, `running`, listener ports and restart status.

Available overrides:

| Option | Meaning |
| --- | --- |
| `--enabled true/false` | Enable/disable this chamber's managed access |
| `--port N` | Internal HTTP target port; defaults to chamber port |
| `--host NAME`, `--chamberlain NAME` | Override global identity labels |
| `--subdomain NAME` | Prefix identity; repeat for aliases, e.g. `--subdomain ai --subdomain dashboard` |
| `--domain NAME` | Replace the base `chamberlain.host` domain; subdomains still prefix it |
| `--proxy true/false` | Explicit proxy/direct choice; identity overrides imply proxy by default |
| `--https true/false` | Override the global HTTPS preference |
| `--redirect true/false` | Redirect ordinary HTTP requests to HTTPS (default true when using HTTPS) |
| `--lan true/false` | Explicit LAN opt-in; default false |
| `--lan-address IP` | Specific assigned RFC1918 IPv4 interface for LAN listening |
| `--reset` | Clear all overrides and restore inheritance/direct mode |

An explicit local HTTPS setting takes precedence over the global preference. With redirects disabled and HTTPS enabled, both listeners proxy to the target. Status reports configured HTTPS separately from measured proxy/DNS status. `--enabled false` disables Pistol access; it does not shut down the application's own port. Remove old hosts records explicitly when retiring or renaming access points (`--dns remove` works by chamber marker, even after reset).

## DNS and LAN access

DNS resolution and reverse proxying are separate. Configuration alone cannot make a custom name resolve.

```powershell
pistol lecap --dns plan
# In an elevated terminal, using the same Windows account:
pistol lecap --dns install
# To remove only this chamber's managed entries:
pistol lecap --dns remove
```

The initial backend writes scoped, marked entries to the local hosts file and saves `hosts.pistol-backup` before the first change. It rejects conflicts with other entries and requires administrator rights before any write. Use `--dns remove` before deleting a chamber. The `DNSBackend` interface allows future resolver implementations. No adapter, router, public DNS, UPnP, or firewall changes occur.

For trusted LAN devices:

```powershell
pistol lecap --lan true --lan-address 192.168.1.25
pistol lecap --dns plan
pistol lecap --serve
```

Use an address actually assigned to this computer. Pistol rejects wildcard and public bind addresses. Only LAN-enabled routes accept private LAN peers; local-only routes remain restricted even when other chambers enable LAN. Default/direct access remains owned by the application's listener; `--lan` alone does not rebind an application. Configure a proxy route for managed LAN access.

Manually install the plan's LAN records on trusted devices or private DNS. If Windows Firewall blocks access, an administrator must review a rule limited to the intended private interface, private profile, ports and trusted remote subnet. Pistol does not create firewall rules. Do not forward these ports on a router or configure a public tunnel. A private bind and peer filter cannot protect against an external router/tunnel that disguises public traffic as private traffic; that network configuration is outside Pistol's control.

## HTTPS and trust

The global PKI is `%LOCALAPPDATA%\Pistol\pki`, outside projects/chambers and Git repositories. It uses an ECDSA P-256 CA, SHA-256 signatures, SAN leaf certificates with server authentication usage, and TLS 1.2 or newer. Windows restricts the directory DACL to the current user and SYSTEM before writing keys. Unix uses directory mode 0700 and key mode 0600. All private keys stay in that directory and inherit its restrictions. Keep the runtime data directory outside any repository or shared folder.

The CA lasts ten years; leaves last 90 days and renew when fewer than 30 days remain, or when stored leaf validation fails. Names, signature, validity, purpose and key match are validated before loading the server context. Startup refuses an invalid CA or one with less than 91 days remaining. It never silently replaces a trusted root. For planned root rotation, stop the proxy, remove the old public CA from enrolled trust stores, securely retire the old PKI directory, run `pistol config --https true`, then enroll the new public CA on each device. Never distribute the private keys.

Trust enrollment is explicit. The first interactive HTTPS enablement may offer the current-user enrollment prompt. `--trust-ca` remains the explicit command for scripts or later setup and checks the exact public CA thumbprint first, so repeating it does not change an already trusted store:

```powershell
# Adds only to this user's Windows Root store; Windows may prompt or policy may deny it.
pistol config --trust-ca
# Exports ONLY the public certificate, refusing to overwrite an existing file.
pistol config --export-ca C:\Users\Keller\Desktop\pistol-local-ca.crt
```

On other platforms, manually enroll the exported certificate in the OS/browser trust store. Other devices and browsers with independent stores need their own manual enrollment. Pistol does not bypass browser trust or disable TLS verification. The CLI reports Windows policy/elevation failures with the failed operation.

## Verification

```powershell
pistol lecap --verify
pistol lecap --verify --json
```

Checks include actual name resolution and expected addresses, target TCP reachability, matching proxy configuration, HTTPS listener, verified TLS handshake, certificate dates and SANs, default OS/Python trust, redirect path/query preservation and an upstream response through the configured route. A valid response from the application can be a 404/405/500; route verification reports the status and proves forwarding, not application health. A proxy-generated 502 fails the route check. Verification uses `HEAD /` at the upstream and a reserved `/.well-known/pistol-lecap` proxy health endpoint.

Cryptographic validation against the explicitly selected Pistol CA and OS trust are reported separately. Missing OS trust fails overall verification even if the certificate is correctly signed. Verification never issues certificates, installs trust or edits DNS. Exit code is 0 on success, 1 on check failure, or 2 on an operational/configuration error. LAN reachability, DNS and browser trust from another device must also be checked on that device; local checks do not claim to prove them.

## Presentation and implementation limits

The installer and interactive chamber creation use `pistol/install_backend.py` for the original logo, green/cyan/light-blue palette and progress bar. Before downloaded source is available, the installer prints a plain preparation message. Redirected output has no ANSI sequences, carriage-return redraws or delays. Non-truecolor terminals use basic colors; terminals unable to encode the logo get an ASCII fallback.

The internal `python -B -m pistol _init` command displays only this presentation with no filesystem changes, installation, configuration writes or networking. It is absent from parser choices, help and completion and bypasses logging/config initialization. `pistol _init` is also available; use the `python -B` form for strict zero-write checks because Python itself can create import caches before CLI dispatch on a cold installation.

The proxy is a local development HTTP/1.1 server, with bounded concurrent connections, request timeouts and a 16 MiB request-body limit. It preserves request paths, query strings, methods and response headers, strips connection-specific headers, and replaces forwarded headers with values established by Pistol. It does not implement WebSocket upgrades, HTTP/2, CONNECT tunnels, or chunked request bodies; unsupported upgrades/body encodings receive 501. There is no service installation or automatic startup. Those protocol extensions and platform-wide DNS daemons require a separate design rather than implicit networking changes.
