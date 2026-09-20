FROM eclipse-temurin:21-jdk-jammy AS java_toolchain
FROM maven:3.9.11-eclipse-temurin-21 AS maven_toolchain
FROM gradle:8.14.3-jdk21 AS gradle_toolchain

# Preserve the accepted Qwen runtime; verify the combined image on the server.
FROM ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10

USER root

COPY --from=java_toolchain /opt/java/openjdk /opt/java/openjdk
COPY --from=maven_toolchain /usr/share/maven /opt/maven
COPY --from=gradle_toolchain /opt/gradle /opt/gradle

ENV JAVA_HOME=/opt/java/openjdk \
    MAVEN_HOME=/opt/maven \
    GRADLE_HOME=/opt/gradle \
    GRADLE_USER_HOME=/workspace/.gradle \
    MAVEN_CONFIG=/workspace/.m2 \
    PATH=/opt/java/openjdk/bin:/opt/maven/bin:/opt/gradle/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends git bash curl unzip ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin agent \
    && install --directory --owner=agent --group=agent --mode=0750 \
        /workspace \
        /workspace/.agent \
        /workspace/.agent/skills \
        /workspace/.agent/tools \
        /workspace/.qwen-home

COPY --chown=agent:agent agent-runtime /usr/local/bin/agent-runtime
COPY capabilities.mjs /usr/local/lib/uar/capabilities.mjs
COPY workspace-operations.mjs /usr/local/lib/uar/workspace-operations.mjs
RUN install --directory --mode=0555 /usr/local/share/uar/empty-hooks
RUN chmod 0555 /usr/local/bin/agent-runtime
RUN node /usr/local/lib/uar/capabilities.mjs

LABEL org.opencontainers.image.title="universal-agent-runtime-qwen" \
      org.opencontainers.image.description="Non-root Qwen Code and Java 21 development runtime" \
      org.opencontainers.image.version="0.1.0" \
      io.universal-agent-runtime.qwen-code-version="0.23.1" \
      io.universal-agent-runtime.java-version="21" \
      io.universal-agent-runtime.qwen-code-image="ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10"

ENV HOME=/workspace/.qwen-home \
    UAR_WORKSPACE=/workspace \
    UAR_SKILLS_DIR=/workspace/.agent/skills \
    UAR_TOOLS_DIR=/workspace/.agent/tools

WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/agent-runtime"]
CMD ["serve"]
