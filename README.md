# stpp-connector
Connector for STPP for IL5

## Run the demo stack

```
make up        # build + start (postgres, integration-api, mcp-server, fake-wdp, frontend)
make seed      # seed rfff_seed from data/mock, then wdp keyed on its ORCIDs/UEIs
make test      # pytest across services (hermetic, in-image)
make tokens    # bake the two demo JWTs into the frontend (required before first use)
make anchors   # compute demo anchors (consumed by make demo)
make demo      # print the three demo queries (anchors substituted) + test JWTs
make verify-phase-7   # role-filtered tools/list, forged-call denial, audit records
```

See `CLAUDE.md` and `docs/architecture.md` for architecture and build order.
