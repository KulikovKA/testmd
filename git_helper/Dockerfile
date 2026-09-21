FROM python:3.12-alpine
RUN apk add --no-cache git openssh-client
# Namespace packages deliberately omit the API package initializers: this helper
# needs only Python stdlib, Git and SSH, never Docker socket/API dependencies.
COPY src/universal_agent_runtime/domain/identifiers.py src/universal_agent_runtime/domain/development_task.py src/universal_agent_runtime/domain/development_trace.py /opt/universal_agent_runtime/domain/
COPY src/universal_agent_runtime/application/ports/trusted_git.py /opt/universal_agent_runtime/application/ports/
COPY src/universal_agent_runtime/adapters/trusted_git_helper.py /opt/universal_agent_runtime/adapters/
ENV PYTHONPATH=/opt PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "universal_agent_runtime.adapters.trusted_git_helper"]
