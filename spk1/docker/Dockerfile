# SPK-1 experiment 1: the agent host as gs-host, agents as gs-agent-1, in the pinned agent-host base image.
FROM node:24.22.0-trixie-slim@sha256:7f486cabe892bff8fef04c0f7da8dba84a560be3e4e2740b7a86afc57ebec315
RUN groupadd --system --gid 10100 gs-workspaces \
 && groupadd --system --gid 10002 gs-host \
 && useradd --system --uid 10002 --gid gs-host --groups gs-workspaces --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin gs-host \
 && groupadd --system --gid 10201 gs-agent-1 \
 && useradd --system --uid 10201 --gid gs-agent-1 --home-dir /var/lib/grindstone/agents/gs-agent-1/home --no-create-home --shell /usr/sbin/nologin gs-agent-1 \
 && install -d -o root -g gs-workspaces -m 2771 /var/lib/grindstone/workspaces \
 && install -d -o gs-agent-1 -g gs-workspaces -m 2750 /var/lib/grindstone/workspaces/run-1 \
 && install -d -o gs-agent-1 -g gs-agent-1 -m 0700 /var/lib/grindstone/agents/gs-agent-1 /var/lib/grindstone/agents/gs-agent-1/home /var/lib/grindstone/agents/gs-agent-1/config \
 && install -d -o gs-host -g gs-host -m 0700 /run/gs-host
COPY package.json /opt/spk1/package.json
COPY node_modules /opt/spk1/node_modules
COPY src /opt/spk1/src
WORKDIR /opt/spk1
# Starts as root only to become gs-host with CAP_SETUID and CAP_SETGID as ambient capabilities; nothing else.
ENTRYPOINT ["setpriv", "--reuid=gs-host", "--regid=gs-host", "--init-groups", "--inh-caps=-all,+setuid,+setgid", "--ambient-caps=-all,+setuid,+setgid", "node"]
CMD ["/opt/spk1/src/exp1-inner.mjs"]
