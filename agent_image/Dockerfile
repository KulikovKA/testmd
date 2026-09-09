# The digest is the verified Qwen Code 0.23.1 release from TASK-005/TASK-006.
FROM ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10

USER root

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin agent \
    && install --directory --owner=agent --group=agent --mode=0750 \
        /workspace \
        /workspace/.agent \
        /workspace/.agent/skills \
        /workspace/.agent/tools \
        /workspace/.qwen-home

COPY --chown=agent:agent agent-runtime /usr/local/bin/agent-runtime
RUN chmod 0555 /usr/local/bin/agent-runtime

LABEL org.opencontainers.image.title="universal-agent-runtime-qwen" \
      org.opencontainers.image.description="Minimal non-root Qwen Code agent runtime image" \
      org.opencontainers.image.version="0.1.0" \
      io.universal-agent-runtime.qwen-code-version="0.23.1" \
      io.universal-agent-runtime.qwen-code-image="ghcr.io/qwenlm/qwen-code@sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10"

ENV HOME=/workspace/.qwen-home \
    UAR_WORKSPACE=/workspace \
    UAR_SKILLS_DIR=/workspace/.agent/skills \
    UAR_TOOLS_DIR=/workspace/.agent/tools

WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/agent-runtime"]
CMD ["serve"]
