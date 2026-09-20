# HumanCraft Minecraft mod

## Share a live scan over LAN

HumanCraft can relay one player's accepted live scan through an Open to LAN
world. The publishing client is the only machine that needs the Python backend
and capture devices. Other players receive the bounded visual stream through
Minecraft and keep using the host's authoritative hit and arm-swing handling.

Install the same HumanCraft JAR and Fabric API version on every client. On a
viewer (for example an Ubuntu laptop without Xcode), launch the game once and
set this in `.minecraft/config/humancraft.json`:

```json
{
  "captureEnabled": false
}
```

The equivalent environment setting is
`HUMANCRAFT_CAPTURE_ENABLED=false`. Viewer mode does not connect to a capture
backend and does not create the built-in fixture. The publisher leaves
`captureEnabled` set to `true`, configures `backendUrl`, joins or hosts the
world, and starts live capture with `V`.

The host opens a world with **Pause → Open to LAN**. Other players join the LAN
entry or use **Direct Connection** with the host's local IP and the port shown
in chat. A late joiner receives the latest shared scan automatically. Explicit
clear, publisher disconnect, dimension change, or avatar rebinding removes the
remote visual. When live input merely becomes stale, the last visual remains
visible while interaction state expires.

The relay caps visual updates at 10 Hz, samples at most 12,000 colored points,
and transfers at most 512 KiB per frame in 24 KiB chunks. Landmarks and
colliders are retained, and physical swings continue through the existing
server-validated `hmc:arm_swing` path. This first version supports one intended
publisher in an Open to LAN world; simultaneous publishers and dedicated-server
certification are not part of the demo contract.

## Run with Sentry

The Gradle development launcher automatically resolves and attaches the Sentry
OpenTelemetry agent and loads `sentry.properties`:

```bash
cd minecraft-mod
./gradlew runClient
```

To send the intentional installation-test exception and three test metrics once
at startup, enable the opt-in verification hook:

```bash
cd minecraft-mod
HUMANCRAFT_SENTRY_VERIFY=true ./gradlew runClient
```

In Sentry, search for the exception `HumanCraft Sentry installation test` and
the metrics `humancraft.sentry_verify`, `humancraft.queue_size`, and
`humancraft.response_time`. Without `HUMANCRAFT_SENTRY_VERIFY=true`, the test
exception and metrics are not emitted.

## Upload source context

Create an organization token in Sentry, keep it out of the repository, and run:

```bash
cd minecraft-mod
export SENTRY_AUTH_TOKEN="your-organization-token"
./gradlew sentryUploadSourceBundleJava
```

The organization token is private and is used only by the build plugin. It is
different from the client-ingest DSN in `sentry.properties`.

## Packaged Minecraft launch

For a launcher-managed Minecraft installation, download
`sentry-opentelemetry-agent-8.57.0.jar` and add these JVM arguments, replacing
the paths with absolute paths on the machine running Minecraft:

```text
-javaagent:/absolute/path/to/sentry-opentelemetry-agent-8.57.0.jar
-Dsentry.properties.file=/absolute/path/to/sentry.properties
-Dotel.traces.exporter=none
-Dotel.logs.exporter=none
-Dotel.metrics.exporter=none
```

The checked-in development settings enable default PII and sample all traces
and profiling sessions. Review `sentry.properties` before distributing a
production build.

For the complete cross-service instrumentation and fault-injection guide, see
[`../docs/observability.md`](../docs/observability.md).
