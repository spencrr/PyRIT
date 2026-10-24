FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392

ARG COPILOT_VERSION=1.0.93
RUN apt-get update && apt-get install --yes --no-install-recommends ca-certificates \
    && npm install --global @github/copilot@${COPILOT_VERSION} \
    && mkdir -p /workspace /home/agent

COPY assets/agent_receipt/orders.json /workspace/orders.json
ENV HOME=/home/agent
ENV COPILOT_HOME=/home/agent/.copilot
WORKDIR /workspace
ENTRYPOINT ["copilot"]
CMD ["--acp", "--stdio"]
