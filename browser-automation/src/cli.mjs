/**
 * CLI helper for manual scenario tests.
 * Usage: node src/cli.mjs <action> [query]
 */
import { playYoutube } from "./scenarios/youtube.mjs";
import { login as spotifyLogin, search as spotifySearch } from "./scenarios/spotify.mjs";
import { scrollSpeedTest } from "./scenarios/scroll.mjs";
import { closeBrowser } from "./browser.mjs";

const [action, ...rest] = process.argv.slice(2);
const query = rest.join(" ").trim();

async function main() {
  let result;
  switch (action) {
    case "youtube-play":
      result = await playYoutube(query || "AC/DC Back in Black", { service: "youtube" });
      break;
    case "youtube-music-play":
      result = await playYoutube(query || "AC/DC Back in Black", { service: "youtube_music" });
      break;
    case "spotify-login":
      result = await spotifyLogin({});
      break;
    case "spotify-search":
      result = await spotifySearch(query || "AC/DC", { play: true });
      break;
    case "scroll-test":
      result = await scrollSpeedTest({
        url: query || "https://en.wikipedia.org/wiki/AC/DC",
      });
      break;
    default:
      console.error("Actions: youtube-play | youtube-music-play | spotify-login | spotify-search | scroll-test");
      process.exit(1);
  }
  console.log(JSON.stringify(result, null, 2));
  // Keep browser open for inspection unless CLOSE=1
  if (process.env.CLOSE === "1") await closeBrowser();
}

main().catch(async (e) => {
  console.error(e);
  await closeBrowser();
  process.exit(1);
});
