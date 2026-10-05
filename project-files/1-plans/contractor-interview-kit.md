# Contractor Interview Kit

Oct 3, 2026 · Tyler · exported Oct 4, 2026 from the
[live doc](https://claude.ai/code/artifact/73343a90-f40b-4778-9df8-f79033e4813c) (the doc is the
current version; edit it there)

Talk to 5–10 HVAC contractors this fall: show them the Fullscope demo for 10 minutes, then ask the questions below. Their answers decide the radio, who gets alerts, the price and which add-on comes next.

## Who to talk to

Aim for residential service companies with 3–30 technicians that sell maintenance agreements: they feel truck rolls and callbacks most, and are the likely first buyers. Mix in one or two owner-operators and one larger shop to see the range.

- **Best first calls:** people you already know from the trade, supply-house counter regulars, your own former employers or crews.
- **Where else:** local ACCA chapter or supply-house training nights, Facebook/Reddit HVAC business groups, manufacturer dealer meetings.
- **Ask for:** 30 minutes, "I'm building a monitoring tool for residential systems and want a contractor's honest opinion before I go further." Offer coffee or lunch.
- **Talk to the owner or service manager**, not only a technician: they decide what gets bought and what a monthly fee is worth.
- **Before showing anything publicly**, the roadmap's IP track says to see a patent attorney first. Keep these conversations one-to-one; consider a simple NDA for anyone you don't know.

## Before the call

The demo runs on your PC with your normal cloud; it is a separate demo account and sends no email.

1. Start the **HVAC Monitor** shortcut (broker, dashboard, cloud).
2. Double-click `hvac-cloud\start-demo.bat`. The first time it builds the demo (about a minute), then it keeps the seven homes live in a window titled "HVAC demo – leave this open" and shows the two demo sign-ins.
3. If the demo was set up before the Rivera home existed (before Oct 4), close the demo window, run `python demo.py reset` in `hvac-cloud`, rerun the **HVAC Monitor** shortcut so the cloud has the thermostat code, and start `start-demo.bat` again.
4. In the browser, sign in as the **demo contractor** (`demo@fullscope.example`). Open a second browser window (or a private window) signed in as the **Garcia homeowner** (`garcia@fullscope.example`). The passwords are in `hvac-cloud\demo-login.txt`.
5. Have this doc open on a tablet or printed, with the notes sheet at the end.
6. Showing it in person: laptop on the table. Over video: share only the browser window.

To start over with a fresh demo: run `python demo.py reset` in `hvac-cloud`, then `start-demo.bat` again.

## The 10-minute demo

Show, then stop talking: the point is their reaction, not the pitch. Ask questions 1–5 first so their answers aren't shaped by what you show.

1. **Fleet page (1 min).** Seven customer systems, worst first. "Which of these would you send a truck to today?" Watch what they look at.
2. **Brooks home (2 min).** Superheat near zero, liquid floodback risk, marked *Service needed*. Show the live trend, the refrigerant table, and the alert log (when it started, Acknowledge).
3. **Patel home (1 min).** High superheat and subcooling below the nameplate target: a slow leak caught before the customer calls. Point at the Equipment page's TXV target.
4. **Miller rental (1 min).** Condensing 36°F+ over ambient: dirty coil. "Would you sell a coil cleaning off this?"
5. **Thompson residence (1 min).** Low delta-T and a filter 118 days old: something the homeowner can fix themselves.
6. **Garcia homeowner view (2 min).** Switch to the homeowner window: plain words, five health areas, maintenance with "I changed it", *Request service*, recent service visits.
7. **Rivera home: thermostat and electrical (2 min, after question 16).** Say first that these are designed and working in software, with no hardware yet. On the thermostat card, open *See the thermostat's screen*: the Alerts tab shows "A part in your outdoor unit is wearing out" in plain words. Then Service (PIN 0000): the pressure and air trend with the weak-capacitor marker where it started. Back in the technician view, Operating state shows the capacitor at 38 of 45 µF. "Would you replace that capacitor before it fails? Would your customers want this on their wall?"
8. **Nguyen casita (30 s).** An indoor monitor offline: how a dropped sensor looks without crying wolf.
9. **Sensors & calibration and Service history (1 min).** Zero and span from the browser; a visit logged with the readings attached.

Don't demo what doesn't exist yet (the radio): ask about it in the questions instead. The thermostat and electrical module work in software but have no hardware yet: show Rivera only after asking question 16, so their first answer isn't shaped by the demo.

## Questions

Ask about what they do today, not what they'd buy: "tell me about the last time…" beats "would you use…". Write down numbers and their exact words.

### Before the demo: their business today

1. How many techs, how many service calls a week in summer, and how many maintenance agreements do you carry?
2. Tell me about the last callback or emergency call that could have been caught earlier. What did it cost you?
3. What share of no-cool calls turn out to be a capacitor, contactor, dirty filter or coil, or low charge?
4. Do you use any monitoring or smart-thermostat alerts today? What do you like and hate about them?
5. How do you find out a customer's system has a problem: they call, you find it on a PM, something else?

### After the demo: value

6. Which of those six homes would you act on first, and what would you do?
7. Which alerts would save you a truck roll or win you a job? Which would you ignore?
8. What's missing that would make you install this on a customer's system tomorrow?

### Alerts

9. Who should get each kind of alert: owner, dispatcher, the tech on the account, the homeowner? Email, text, an app?
10. How many alerts a week per 100 customers is too many?
11. Should the homeowner see problems at all, or only the contractor first?

### Installing it

12. How long can an install take before it isn't worth it on a PM visit: 15 minutes, 30, an hour?
13. Who would install it: a senior tech, anyone on a PM, the installer at changeout?
14. Homeowner WiFi at the condenser and in the attic: how often is it a problem? Would a plug-in gateway that talks to the units by radio be worth $20–30 more?

### Add-ons

15. Electrical monitoring (compressor and fan amps, capacitor health, contactor wear) without opening the panel: how much would that be worth on top?
16. A thermostat that's part of the system (scheduling plus the diagnostics): would you rather sell that, or keep the customer's own thermostat?

### Price and business model

17. What would you pay per system per month, and would you pass it to the homeowner, bundle it into a maintenance agreement, or absorb it?
18. Hardware: what's acceptable up front per system, and should it be yours or the homeowner's?
19. Would this help you sell more maintenance agreements? How many more, roughly?

### Wrap-up

20. Would you put it on 3–5 of your customers' systems as a pilot this cooling season, at no cost, in exchange for feedback?
21. Who else should I talk to?

## What the answers decide

Each open roadmap decision, the questions that settle it, and the answer that would tip it. After 5 interviews, tally the notes sheet against this table.

| Decision | Questions | Tips toward |
|---|---|---|
| Radio: LoRa + gateway, WiFi only, or cellular | 12, 13, 14 | WiFi trouble at the unit is common and installs must be fast → LoRa + gateway; rarely a problem → stay WiFi; no-WiFi homes matter and a monthly fee is fine → cellular |
| Who gets which alert, and how | 9, 10, 11 | Today: homeowners get plain email, the contractor the technical one. Wanting texts, a dispatcher queue or contractor-first → change the routing before the pilot |
| Price per system per month, and who pays | 17, 18, 19 | Sets the subscription and whether hardware is sold or bundled; if most bundle it into agreements, price per agreement, not per homeowner |
| Electrical module: when | 3, 15 | Capacitors and contactors are a big share of no-cool calls and they'd pay extra → version 1 (current sensors only) goes on carrier rev B |
| Display thermostat: build or not | 16 | Most prefer to keep the customer's thermostat → park it; some want to sell one → design it after the pilot |
| What "done" means for the pilot | 6, 7, 8 | The most-asked missing feature becomes the last item before the pilot |
| Pilot partners | 20, 21 | Two or three yeses → schedule installs for the next cooling season |

## Notes sheet

One row per interview; the question numbers are in the headers. Add rows as you go.

| # | Contractor, company | Techs / agreements (Q1) | Costly preventable call (Q2) | Alerts that matter (Q7) | Who gets alerts (Q9) | OK install time (Q12) | WiFi trouble at units (Q14) | $ per system per month (Q17) | Pilot (Q20) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | |
| 2 | | | | | | | | | |
| 3 | | | | | | | | | |
| 4 | | | | | | | | | |
| 5 | | | | | | | | | |
| 6 | | | | | | | | | |
| 7 | | | | | | | | | |
| 8 | | | | | | | | | |
| 9 | | | | | | | | | |
| 10 | | | | | | | | | |
