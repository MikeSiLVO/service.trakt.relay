# Trakt Relay

Trakt Relay scrobbles what you watch in Kodi to [Trakt](https://trakt.tv) and syncs your library with it. It runs on Kodi 21 (Omega) and later.

## What it does

- Scrobbles movies, episodes, live TV and recordings as you watch them.
- Syncs watched status and ratings in the direction you choose, resume points from Trakt, and your collection to Trakt. A sync asks before it unwatches or clears more than 20 items in Kodi, and leaves them as they are when it runs in the background.
- Asks for a rating after you watch, when that is switched on.
- Adds a Trakt entry to the context menu: rate, add to the watchlist, start a rewatch of a show, or sync the whole library.
- Sends a finished play to Trakt later when Trakt can't be reached at the time.

## Getting started

Install Trakt Relay from the Kodi add-on repository. Betas come first through [repository.silvo](https://mikesilvo.github.io/repository.silvo/).

Until your Trakt account is connected, each Kodi start shows a code and a QR code to connect it. Approve it at [trakt.tv/activate](https://trakt.tv/activate) and the dialog closes by itself. You can also connect from the add-on settings.

## What gets recognized

Library items are matched by the IMDb, TMDB and TVDB ids your scrapers give them. Streams from other add-ons are recognized from the season, episode, title and year the add-on sets, or from ids it passes.

For add-on developers, Trakt Relay reads the same properties as script.trakt:

| Property | Where | Effect |
|---|---|---|
| `script.trakt.ids` | Home window property | JSON ids for the playing item, like `{"tmdb": 550}` |
| `script.trakt.exclude` | ListItem property | The item is never scrobbled |
| `script.trakt.paused` | Home window property | `true` pauses scrobbling while "Add-ons can pause scrobbling" is on |

## Running a sync from outside Kodi

```bash
curl -s -H 'Content-Type: application/json' http://localhost:8080/jsonrpc -d '{
  "jsonrpc": "2.0", "id": 1, "method": "Addons.ExecuteAddon",
  "params": {"addonid": "service.trakt.relay",
             "params": {"action": "sync", "silent": "true", "library": "movies"}}}'
```

`silent` hides the progress dialog, and `library` limits the sync to `movies` or `episodes`. Leave either out for the defaults: the dialog shows and both are synced. A silent sync can't ask, so it skips any change it would ask about.

## Problems

Report problems on the [issue tracker](https://github.com/MikeSiLVO/service.trakt.relay/issues) with a debug log. Turn on debug logging in Kodi and "Enable debug mode" in Trakt Relay's settings first, then reproduce the problem.

## Credits and license

Trakt Relay is a modified version of [script.trakt](https://github.com/trakt/script.trakt) 3.9.0 by Trakt.tv, Razzeee and its contributors and translators. It is distributed under the GNU General Public License version 2 only; see [LICENSE.txt](LICENSE.txt) and [NOTICE.txt](NOTICE.txt).

Trakt Relay is not affiliated with or endorsed by Trakt. The Trakt name and logos are trademarks of Trakt.
