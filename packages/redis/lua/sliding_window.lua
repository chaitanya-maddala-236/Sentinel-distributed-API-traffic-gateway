-- sliding_window.lua
--
-- Atomic sliding-window-log rate limiter using a Redis sorted set.
--
-- KEYS[1] = window key, e.g. "rl:ip:{ip}:route:{id}"
--
-- ARGV[1] = limit           (max requests allowed inside the window)
-- ARGV[2] = window_ms       (window size in milliseconds)
-- ARGV[3] = now_ms          (current time in ms, caller-supplied — see
--                             token_bucket.lua for why we don't use
--                             redis.call("TIME") here)
-- ARGV[4] = member_id       (unique id for this request, e.g. request_id;
--                             used as the sorted-set member so concurrent
--                             requests in the same millisecond don't
--                             collide on score)
-- ARGV[5] = ttl_seconds     (idle expiry for the window key)
--
-- Returns:
--   [1] allowed          (1 or 0)
--   [2] current_count    (requests in window AFTER this evaluation)
--   [3] retry_after_ms   (0 if allowed, else estimated wait until the
--                          oldest entry ages out of the window)
--
-- Sequence (all atomic within one Lua execution):
--   ZREMRANGEBYSCORE  -- drop entries older than the window
--   ZCARD             -- count what remains
--   if under limit: ZADD the new request, else reject without adding it
--
-- Rejected requests are NOT added to the set — only admitted requests
-- consume a slot. This gives exact sliding-window semantics rather than
-- the looser "N buckets averaged" approximation.

local window_key = KEYS[1]

local limit        = tonumber(ARGV[1])
local window_ms     = tonumber(ARGV[2])
local now_ms        = tonumber(ARGV[3])
local member_id     = ARGV[4]
local ttl_seconds   = tonumber(ARGV[5])

local window_start = now_ms - window_ms

redis.call("ZREMRANGEBYSCORE", window_key, "-inf", window_start)

local current_count = redis.call("ZCARD", window_key)

local allowed = 0
local retry_after_ms = 0

if current_count < limit then
    redis.call("ZADD", window_key, now_ms, member_id .. ":" .. now_ms)
    current_count = current_count + 1
    allowed = 1
else
    -- Retry-After: time until the oldest entry falls out of the window.
    local oldest = redis.call("ZRANGE", window_key, 0, 0, "WITHSCORES")
    if oldest[2] ~= nil then
        local oldest_score = tonumber(oldest[2])
        retry_after_ms = (oldest_score + window_ms) - now_ms
        if retry_after_ms < 0 then
            retry_after_ms = 0
        end
    end
end

redis.call("EXPIRE", window_key, ttl_seconds)

return {allowed, current_count, retry_after_ms}
