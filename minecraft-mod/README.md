# HumanCraft Minecraft mod

## Hacker Badge controller

The client also connects to `ws://127.0.0.1:8000/ws/controller`. The D-pad
controls yaw/pitch, A moves forward, B jumps, the Aux1 side switch enables
sprint, HOME arms/disarms the controller, and each calibrated punch invokes one
normal Minecraft attack. Keyboard and mouse input remain active.

Set `HUMANCRAFT_CONTROLLER_URL` to override the controller socket. The defaults
are a 250 ms stale-input fail-safe, 120 degrees/second yaw, and 90
degrees/second pitch; these can also be adjusted in `humancraft.json`.

Controller input is suppressed while chat or another screen is open, the game
is unfocused, or the player is dead. The HumanCraft HUD reports badge
connection, armed state, packet age, and punch count.

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
