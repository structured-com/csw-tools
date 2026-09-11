# `csw-tools sync-collection-rules`

Status: not implemented; DEV/TESTING only.

The command is registered so its name and future location remain stable, but it
does not validate or modify collection rules yet.

```console
csw-tools -d my-company sync-collection-rules
```

The current placeholder resolves and displays the selected dashboard, then exits
nonzero with `not implemented yet`. It has no command-specific options, input
schema, mutation behavior, or recovery support. Do not use it in production or
build automation around its placeholder behavior.

Implementation belongs in this directory. Follow
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md) for shared entrypoint,
configuration, API, output, and testing conventions.
