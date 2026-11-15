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
- FilaMan **2.0.5 or newer**. The package carries its own sidebar icon as an
  SVG, and FilaMan accepts an SVG in a plugin ZIP only from 2.0.5 on. An older
  FilaMan refuses the upload; version 0.1.1 is the last one that installs on
  FilaMan 1.2.45 to 2.0.4.

## Installation

From FilaMan's plugin catalog: open Admin, Plugins, Browse Plugins, and
install "SpoolmanScale".

By hand:

1. Download `spoolmanscale-<version>.zip` from the releases, or build it
   yourself with `python3 tools/build_zip.py`.
2. In FilaMan, open Admin, Plugins, and upload the ZIP.

The entry "SpoolmanScale" appears in the navigation. FilaMan caches the
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

## Saving files from the scale

FilaMan shows plugin pages in a sandboxed frame that does not allow downloads,
and the scale's frame inherits that. Saving a log or a screenshot therefore
does nothing inside FilaMan, and a tab opened from the frame carries the same
restriction. Open the scale through its own address to save files.

## More than one device

The plugin offers every device that is online in FilaMan, because FilaMan's
list of active devices does not say what kind of device each one is. A scale
that is not a SpoolmanScale shows up as a button too and opens its own web
page, if it has one. With more than one device the plugin remembers the last
one picked.

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
