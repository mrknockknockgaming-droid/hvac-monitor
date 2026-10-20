# Feedback and future changes

Things Tyler wants to change or remember for later, from using the system. New items go
under Open; move them to Planned once they are scheduled (name the roadmap phase) and to
Done with the date and PR when finished. Newest first in each section.

Entry format: `- YYYY-MM-DD · area · note · status`
Areas: hardware, enclosure, firmware, web app, dashboard, cloud, install, docs, business.

## Open

- 2026-10-19 · install · WiFi is weak at both units (outdoor -68 dBm with drops every few minutes, indoor -79 dBm); WiFi uploads failed at 7-42 %. Move the router or add an access point nearer the units, or wait for LoRa on rev B · open
- 2026-10-11 · hardware · Sensor leads need to be longer, especially for package units · open
- 2026-10-11 · install · Reserve 192.168.1.50 (the Proxmox container the nodes report to) in the router's DHCP; the PC's address no longer matters to the nodes · open
- 2026-10-11 · firmware · OTA password is weak: change it in config.h and reflash · open

## Planned

- 2026-10-20 · business · Flash boards from the browser in Fullscope Office, with firmware 0.4.0 keeping the kit code in flash · done (PR #84)
- 2026-10-20 · business · Billing in Fullscope Office: manual invoices first · done (PR #86, release office-1.0)

## Done

- 2026-10-20 · business · One page for the business side: email requests, inquiries and support in one inbox, customers, warranties, and QR codes contractors scan to add a shipped kit to their account and homeowner · done 2026-10-20 (Fullscope Office, PR #80 and #81)

- 2026-10-11 · cloud · Move the MQTT broker to a Proxmox VM and point both nodes at it · done 2026-10-14 (Proxmox container 192.168.1.50, PR #40)
- 2026-10-12 · web app · Select one or more alerts and highlight the chart at the time each alert occurred · done 2026-10-17 (tick alerts in the Monitor alert log)
- 2026-10-11 · install · Land the Y and O/B call wires if not done (diagnostics only run during a call) · done 2026-10-17 (Tyler)
