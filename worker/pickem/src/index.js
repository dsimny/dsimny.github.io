/**
 * Open Ledger Sports — pick'em Worker.
 *
 * Three routes, no always-on process, free tier:
 *   POST /interactions   Discord's interactions endpoint (Ed25519-verified).
 *                        Handles PING and the two pick'em buttons.
 *   GET  /entries?date=  The day's entries for the nightly grader.  Bearer READ_TOKEN.
 *   POST /results        Per-user graded results from the grader; updates the
 *                        standings table and returns named leaderboards for the
 *                        Discord post.  Bearer READ_TOKEN.
 *
 * Division of labor, on purpose: this Worker is a dumb inbox and scoreboard.
 * All grading decisions (winner, lock enforcement, ride/fade mapping) happen in
 * the repo's Python pipeline where they are reviewable and reproducible; the
 * Worker never decides who won.  If the Worker is down, entries pause — the
 * board, ledger, and site are untouched.
 *
 * Secrets: DISCORD_PUBLIC_KEY (hex, from the Discord app page), READ_TOKEN
 * (any long random string; the same value goes in the GitHub Actions secret
 * PICKEM_READ_TOKEN).  D1 binding: DB (see schema.sql).
 */

const EPHEMERAL = 64;

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

async function verifyDiscord(request, bodyText, env) {
  const sig = request.headers.get("x-signature-ed25519");
  const ts = request.headers.get("x-signature-timestamp");
  if (!sig || !ts) return false;
  try {
    const key = await crypto.subtle.importKey(
      "raw", hexToBytes(env.DISCORD_PUBLIC_KEY), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify(
      "Ed25519", key, hexToBytes(sig), new TextEncoder().encode(ts + bodyText));
  } catch {
    return false;
  }
}

function bearerOk(request, env) {
  return request.headers.get("authorization") === `Bearer ${env.READ_TOKEN}`;
}

function ephemeral(content) {
  return json({ type: 4, data: { content, flags: EPHEMERAL } });
}

async function handleInteraction(body, env) {
  if (body.type === 1) return json({ type: 1 }); // PING -> PONG

  if (body.type === 3) { // MESSAGE_COMPONENT (button press)
    // custom_id: pickem:<ride|fade>:<YYYY-MM-DD>:<lockEpochSeconds>
    const parts = (body.data?.custom_id || "").split(":");
    if (parts[0] !== "pickem" || parts.length !== 4) {
      return ephemeral("Unrecognized button. This one isn't wired to anything.");
    }
    const [, side, date, lockStr] = parts;
    const lock = parseInt(lockStr, 10);
    const now = Math.floor(Date.now() / 1000);
    if (!["ride", "fade"].includes(side) || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !lock) {
      return ephemeral("Malformed button payload — nothing recorded.");
    }
    if (now >= lock) {
      return ephemeral(
        "⏱️ Entries locked at first pitch — this one doesn't count. " +
        "Tomorrow's featured game posts with the morning board.");
    }
    const user = body.member?.user || body.user || {};
    const userId = user.id;
    if (!userId) return ephemeral("Couldn't identify your Discord account — nothing recorded.");
    const userName = body.member?.nick || user.global_name || user.username || "unknown";

    await env.DB.prepare(
      `INSERT INTO entries (date, user_id, user_name, side, ts)
       VALUES (?1, ?2, ?3, ?4, ?5)
       ON CONFLICT(date, user_id) DO UPDATE SET
         side = excluded.side, ts = excluded.ts, user_name = excluded.user_name`)
      .bind(date, userId, userName, side, now).run();

    const { n } = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM entries WHERE date = ?1").bind(date).first();

    const verb = side === "ride" ? "Riding with the engine" : "Fading the engine";
    const mins = Math.floor((lock - now) / 60);
    const till = mins >= 90 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
    return ephemeral(
      `✅ ${verb} for ${date}. ${n} ${n === 1 ? "entry" : "entries"} so far. ` +
      `You can change your pick until first pitch (${till} left). ` +
      `Graded overnight; standings post here every morning. No prizes, no stakes — ` +
      `bragging rights only. 21+ · 1-800-GAMBLER.`);
  }

  return ephemeral("Nothing to do with that interaction type.");
}

async function handleResults(body, env) {
  // body: { date: 'YYYY-MM-DD', results: [{user_id, user_name, result: WIN|LOSS|VOID}] }
  const { date, results } = body;
  if (!date || !Array.isArray(results)) return json({ error: "bad payload" }, 400);
  const month = date.slice(0, 7);
  let applied = 0, skipped = 0;

  for (const r of results) {
    if (!r.user_id || !["WIN", "LOSS", "VOID"].includes(r.result)) continue;
    const row = await env.DB.prepare(
      "SELECT * FROM standings WHERE user_id = ?1").bind(r.user_id).first();
    if (row && row.last_graded_date >= date) { skipped++; continue; } // idempotent re-run
    const cur = row || {
      wins: 0, losses: 0, voids: 0, streak: 0, best_streak: 0,
      month: month, month_wins: 0, month_losses: 0,
    };
    if (cur.month !== month) { cur.month = month; cur.month_wins = 0; cur.month_losses = 0; }
    if (r.result === "WIN") {
      cur.wins++; cur.month_wins++;
      cur.streak = cur.streak > 0 ? cur.streak + 1 : 1;
      cur.best_streak = Math.max(cur.best_streak, cur.streak);
    } else if (r.result === "LOSS") {
      cur.losses++; cur.month_losses++;
      cur.streak = cur.streak < 0 ? cur.streak - 1 : -1;
    } else {
      cur.voids++;
    }
    await env.DB.prepare(
      `INSERT INTO standings (user_id, user_name, wins, losses, voids, streak,
                              best_streak, month, month_wins, month_losses, last_graded_date)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
       ON CONFLICT(user_id) DO UPDATE SET
         user_name = excluded.user_name, wins = excluded.wins, losses = excluded.losses,
         voids = excluded.voids, streak = excluded.streak, best_streak = excluded.best_streak,
         month = excluded.month, month_wins = excluded.month_wins,
         month_losses = excluded.month_losses, last_graded_date = excluded.last_graded_date`)
      .bind(r.user_id, r.user_name || null, cur.wins, cur.losses, cur.voids, cur.streak,
            cur.best_streak, cur.month, cur.month_wins, cur.month_losses, date).run();
    applied++;
  }

  const monthBoard = (await env.DB.prepare(
    `SELECT user_name, month_wins, month_losses, streak FROM standings
     WHERE month = ?1 AND (month_wins + month_losses) > 0
     ORDER BY month_wins DESC,
              CAST(month_wins AS REAL) / (month_wins + month_losses) DESC
     LIMIT 10`).bind(month).all()).results;
  const seasonBoard = (await env.DB.prepare(
    `SELECT user_name, wins, losses, best_streak FROM standings
     WHERE (wins + losses) > 0
     ORDER BY wins DESC, CAST(wins AS REAL) / (wins + losses) DESC
     LIMIT 10`).all()).results;
  return json({ applied, skipped, month_leaderboard: monthBoard, season_leaderboard: seasonBoard });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/interactions" && request.method === "POST") {
      const bodyText = await request.text();
      if (!(await verifyDiscord(request, bodyText, env))) {
        return new Response("invalid request signature", { status: 401 });
      }
      return handleInteraction(JSON.parse(bodyText), env);
    }

    if (url.pathname === "/entries" && request.method === "GET") {
      if (!bearerOk(request, env)) return json({ error: "unauthorized" }, 401);
      const date = url.searchParams.get("date") || "";
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return json({ error: "bad date" }, 400);
      const rows = (await env.DB.prepare(
        "SELECT user_id, user_name, side, ts FROM entries WHERE date = ?1")
        .bind(date).all()).results;
      return json({ date, entries: rows });
    }

    if (url.pathname === "/results" && request.method === "POST") {
      if (!bearerOk(request, env)) return json({ error: "unauthorized" }, 401);
      return handleResults(await request.json(), env);
    }

    return new Response("ols-pickem: nothing here. See /interactions.", { status: 404 });
  },
};
