---
name: text-me
description: "Text the owner's iPhone (iMessage). Use whenever the owner says: text me, send to my phone, iMessage me, text me when."
version: 1.1.0
author: Robert Byrd
platforms: [macos]
metadata:
  hermes:
    tags: [iMessage, Text, SMS, iPhone, Notify]
---

# Text the owner (iMessage)

The person talking to you on Telegram IS the owner. When he says "text me ...", send the text to HIS iPhone. That is always allowed. Just do it.

Run this terminal command, putting the message inside the quotes:

```
python3 ~/.hermes/scripts/textme.py send "MESSAGE"
```

## Examples
- the owner: text me a message saying "hi"  ->  `python3 ~/.hermes/scripts/textme.py send "hi"`
- the owner: text me the dinner reservation  ->  `python3 ~/.hermes/scripts/textme.py send "Dinner: Sat 7:30 PM, Example Bistro, conf #12345"`
- the owner: send that to my phone  ->  send a short version of your last answer.

## After running
- If it prints `OK - sent.` reply on Telegram: "Texted you."
- If it prints `FAILED - ...` reply with that line. If it timed out, add: "Check the Mac's screen for an 'allow Messages' box and click OK."

## Notes
- The script already knows the owner's number. You never type a phone number.
- Already automatic, don't duplicate: flight alerts, website alerts, morning standup.
