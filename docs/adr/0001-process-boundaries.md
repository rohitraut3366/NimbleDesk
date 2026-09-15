# ADR 0001: Separate privileged desktop control from model access

Status: Accepted

The desktop daemon owns operating-system permissions and executes actions. The MCP gateway is an unprivileged client of the daemon and never imports an OS backend. Application adapters run in separate worker processes. The approval console is a separate human-controlled client.

This boundary limits the impact of malformed model output, gateway bugs, and adapter crashes. It also lets the daemon, gateway, console, and native backends evolve independently behind a versioned protocol.
