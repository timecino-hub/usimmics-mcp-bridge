# Installed examples

The canonical JSON files now live in
[`usimmics_bridge/data/examples`](../usimmics_bridge/data/examples). They ship in
both the wheel and source distribution; there is no second editable JSON copy.

Use the installed commands from any directory, including without a repository
checkout or a bridge configuration:

```powershell
usimmics-bridge examples list
usimmics-bridge examples show voltage_divider
usimmics-bridge examples export voltage_divider --output voltage_divider.json
usimmics-bridge examples export mixer --output mixer.json
```

An export fails if its destination already exists. To produce a complete
`build_schematic` request, also pass
`--schematic-path "C:/CircuitProjects/demo_prj/divider.sch"`. The project must
exist and be within a configured root before building it.

The divider can be built and previewed without a simulator. Building the mixer
requires the BAT54 model in the user's uSimmics installation. Native simulation
of either example requires separately installed uSimmics 5.9 on Windows.
