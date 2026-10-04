# Photon: practice the signs from your drive in iMessage

This addition leaves local sign recognition, saved reviews, cached ElevenLabs cues,
and the browser Park quiz intact. After parking, **Practice in iMessage** starts a
separate quiz using up to five unique resolved captures. Ignored/non-sign reviews
and pending detections are excluded. The sign's actual captured picture is sent
with its catalog question (the question text is delivered first, followed by its picture); each reply is graded locally, recorded with
`channel: photon`, and followed by feedback and the next capture. No language
model or cloud vision call is used by this quiz.

## Setup

1. Create a project at https://app.photon.codes/ and enable a managed iMessage line.
   Obtain the project ID and project secret from project Settings. An SDK install
   alone does not provision a phone line.
2. In the root `.env`, set the fields below. Only put a phone number/email address
   the user explicitly approves for this demo in `PHOTON_TEST_RECIPIENT`.

   ```dotenv
   PHOTON_PROJECT_ID=your-project-id
   PHOTON_PROJECT_SECRET=your-project-secret
   PHOTON_TEST_RECIPIENT=approved-imessage-number-or-email
   PHOTON_BRIDGE_TOKEN=one-random-shared-local-secret
   PHOTON_BRIDGE_URL=http://127.0.0.1:3001
   PHOTON_BACKEND_URL=http://127.0.0.1:8000
   ```

   The bridge token is a local secret shared between Python and Node, distinct
   from the Photon project secret. Do not commit `.env`.
3. Install/start the bridge from the repository root:

   ```sh
   npm --prefix photon-bridge ci --ignore-scripts
   npm --prefix photon-bridge start
   ```

   Node 20.12+ supports the script's `--env-file` flag. Node 25.1 was used for
   development. The bridge listens only on localhost. The provider streams inbound
   messages through Spectrum's managed connection; no public webhook tunnel is
   needed. Restart both Python and the bridge after changing credentials.
4. Drive the real clip, park, and click **Practice in iMessage**. This is the only
   action that sends the initial message. Integration status never sends a message.
5. Answer each question in iMessage. `STOP` (uppercase), `stop practice`, or
   `cancel practice` ends the session. Lowercase `stop` can still be a sign answer.

## Status and recovery

- `GET /api/integrations/photon/status`: explicit `needs_setup`, `disconnected`,
  `connecting`, `connection_failed`, or `ready` status. `ready` means Spectrum
  initialized; only successful Send verifies acceptance of a real message.
- `POST /api/photon/practice/start` with `{"drive_id":"current-drive"}`: parked
  only. The optional recipient must equal the configured approved recipient.
- `GET /api/photon/practice/status?drive_id=...`: session progress.
- `POST /api/photon/practice/reply`: authenticated bridge callback, not a public
  webhook. Requires the local bridge bearer token.

Session state and pending outgoing messages are saved under `.local/photon/`.
Press Send again after a delivery error to retry the same session, not create a
second quiz. Incoming message IDs are deduplicated; stale question replies are
ignored. The bridge persists completed image/text sends by idempotency key and
uses Spectrum's provider transport retries. A crash exactly between provider
acceptance and local receipt persistence can still repeat the last message;
this is not an exactly-once distributed transaction.

A new drive/resuming driving blocks further practice replies and outgoing
questions. Starting a fresh parked drive creates its own snapshot. Browser quiz
progress is independent. As with the existing demo, catalog meanings are drafts
and nearest model guesses may be wrong; saved corrections select the intended
sign before the snapshot is built.

## Verification

```sh
.venv-local/bin/python -m unittest tests.test_photon_practice -v
npm --prefix photon-bridge run check
npm --prefix photon-bridge test
```

Fifteen backend lifecycle tests use mocked transport: corrected captures,
exclusions, grading, multiple questions, completion, STOP, duplicate delivery,
recipient/auth restrictions, retries, stale drive/index, and path confinement.
The Node test starts the real installed Spectrum SDK with intentionally absent
credentials, verifies authentication and honest disconnected status, and proves
it refuses Send. These tests **do not prove live iMessage delivery**. Real delivery
and replies require a provisioned line, valid credentials, and an approved
recipient; record that result separately once available.

The pinned `spectrum-ts` 12.10.1 package's npm audit currently reports 14 moderate
transitive findings from OpenTelemetry. Telemetry is not enabled here. No forced
SDK downgrade was applied; recheck advisories before deployment.

## Official SDK references

- https://photon.codes/docs/spectrum-ts/getting-started
- https://photon.codes/docs/spectrum-ts/providers/imessage
- https://photon.codes/docs/spectrum-ts/spaces-and-users
- https://photon.codes/docs/spectrum-ts/content/attachments

The bridge imports `Spectrum`, `attachment`, and
`imessage` from the official `spectrum-ts` package, uses
`imessage(app).space.create(await imessage(app).user(recipient))` for the DM, and
subscribes to `app.messages` for inbound replies. It uses Photon's managed
provider rather than accessing the Mac's personal Messages database.

## Live validation, October 4, 2026

Project credentials successfully minted a shared-mode provider token. The approved
recipient was enrolled in the project dashboard, but the provider rejected both
the live quiz send and read-only availability checks with gRPC
`PERMISSION_DENIED` (7): `Target not allowed for this project`. E.164, digits-only,
and national formats of the same phone number all returned the same denial.
The user subsequently confirmed receiving text questions and feedback after
sending an initial greeting. Proactive sends remained denied in later tests;
shared-line routing now reuses the actual inbound conversation. Captured-photo
paths and greeting handling have regression coverage, but photo delivery still
requires live confirmation. The status API exposes `recipient_not_allowed` after a failed
send and instructions to verify the exact approved number in the project's Users
list. No alternative recipient is contacted.

The bridge tracks safe delivery stages (`resolve_conversation`, `send_text`,
`send_image`, `delivered`) and error codes without logging credentials or phone
numbers. The Python request budget is 75 seconds to accommodate provider calls.
The official HTTP client was tested with a read-only availability request against
the shared provider host and returned HTTP 415; the deployed bridge retains
Spectrum's official gRPC provider rather than switching to an unsupported
transport.
