# SpoolmanScale for FilaMan

A small [FilaMan](https://github.com/Fire-Devils/filaman-system) plugin that
shows the web interface of a [SpoolmanScale](https://github.com/Niko11111/SpoolmanScale)
inside FilaMan, with its own entry in the navigation.

Nothing has to be typed in: the scale reports its address to FilaMan once a
minute, and the plugin takes it from FilaMan's list of active devices. With
more than one device online, a row of buttons switches between them.

## Requirements

- A SpoolmanScale with **FilaMan as its backend**, connected to it.
- SpoolmanScale firmware **0.8.3 or newer**. Older firmware refuses to be
  shown inside another page.
- FilaMan **1.2.45 or newer**, the release that renders plugin pages inside
  its navigation.

## Installation

1. Download `spoolmanscale-<version>.zip` from the releases, or build it
   yourself with `python3 tools/build_zip.py`.
2. In FilaMan, open Admin, Plugins, and upload the ZIP.
3. The entry "SpoolmanScale" appears in the navigation. FilaMan caches the
   navigation for up to ten minutes, so a reload may be needed.

## When the page stays empty

The scale only lets the backend it is configured for show it, and the browser
compares the address exactly:

- **Open FilaMan with the same address that is set on the scale.** If the
  scale uses `192.168.1.50:8002`, opening FilaMan as `filaman.local:8002` or
  through a reverse proxy makes the browser refuse the frame.
- **Use http.** The scale speaks only http, and a page loaded over https may
  not show an http page inside it. The plugin says so and offers to open the
  scale in its own tab instead.
- **Password.** If the scale's web interface has a password, the browser may
  not show the login prompt inside the frame. "Open in own tab" works in that
  case.

## Light and dark

The plugin follows FilaMan's theme and passes light or dark on to the scale
as a `spoolmanscale:theme` message, so the scale's pages switch with it.

## Development

    python3 tools/build_zip.py --check      validate only
    python3 tools/build_zip.py --selftest   prove the validation bites
    python3 tools/build_zip.py              build dist/spoolmanscale-<version>.zip

The checks mirror FilaMan's own package validation, so a ZIP that builds here
is accepted by FilaMan. Bump the version in `plugin.json` and `__init__.py`
together; the build refuses when they disagree.

## License

MIT, see [LICENSE](LICENSE).
