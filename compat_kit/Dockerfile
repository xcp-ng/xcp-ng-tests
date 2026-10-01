FROM fedora:43
RUN dnf install -y --setopt=install_weak_deps=False \
    curl \
    git \
    iputils \
    netcat \
    openssh-clients \
    sshpass \
    uv && \
    dnf clean all
RUN mkdir ~/.ssh && chmod 600 ~/.ssh
WORKDIR /app
COPY pyproject.toml uv.lock .python-version /app/
RUN uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:$PATH"
COPY ./ /app/
RUN chmod 600 compat_kit/id_ed25519*
ENTRYPOINT ["uv", "run", "--no-dev", "./compat_kit/entrypoint.py"]

