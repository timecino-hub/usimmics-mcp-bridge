# Local execution boundaries

The bridge uses local stdio and does not open a network listener. It runs `simulator.exe` from a user-configured installation and reads/writes within configured project roots. Root checks and cooperative locks are safeguards against mistakes, not an OS sandbox or protection from a malicious local administrator.

Only configure installations and projects you trust. Keep credentials and unrelated private files outside project roots. Do not commit config.json, environment files, user run logs or paths. Native editors do not participate in bridge locks; unsaved GUI changes cannot be inspected.

Report reproducible bugs in repository issues using small synthetic circuits. Do not post secrets or private models. For a sensitive issue, use GitHub private vulnerability reporting if enabled rather than a public reproduction.
