-- token_bucket.lua
--
-- Atomic token-bucket rate limiter.
--
-- KEYS[1] = bucket key, e.g. "rl:tenant:{id}" or "rl:apikey:{id}:route:{id}"
--
-- ARGV[1] = capacity           (max tokens the bucket can hold)
-- ARGV[2] = refill_rate        (tokens added per second)
-- ARGV[3] = requested_cost     (tokens this request consumes, usually 1)
-- ARGV[4] = now_ms             (current time in milliseconds, supplied by the
--                                caller so all gateway instances agree on the
--                                same clock reading for this evaluation --
--                                Redis TIME is avoided because it is not
--                                deterministic across replicas/scripts and
--                                because passing it in lets tests control
--                                time deterministically)
-- ARGV[5] = ttl_seconds        (idle expiry for the bucket key)
--
-- Returns a 3-element array:
--   [1] allowed          (1 or 0)
--   [2] remaining_tokens (integer, floored)
--   [3] retry_after_ms   (0 if allowed, else ms until enough tokens exist)
--
-- This script performs the entire read-calculate-update-expire cycle in a
-- single atomic Redis operation. Because Redis executes Lua scripts to
-- completion without interleaving other commands, this eliminates the
-- classic GET -> compute -> SET race condition that would otherwise let
-- concurrent gateway instances both observe stale token counts and both
-- admit a request that should have been rejected.

local bucket_key = KEYS[1]

local capacity      = tonumber(ARGV[1])
local refill_rate   = tonumber(ARGV[2])
local cost          = tonumber(ARGV[3])
local now_ms        = tonumber(ARGV[4])
local ttl_seconds   = tonumber(ARGV[5])

local state = redis.call("HMGET", bucket_key, "tokens", "last_refill_ms")
local tokens = tonumber(state[1])
local last_refill_ms = tonumber(state[2])

if tokens == nil then
    -- First time we've seen this bucket: start full.
    tokens = capacity
    last_refill_ms = now_ms
end

-- Guard against clock skew going backwards (e.g. NTP correction) --
-- never let elapsed time go negative, which would drain the bucket.
local elapsed_ms = now_ms - last_refill_ms
if elapsed_ms < 0 then
    elapsed_ms = 0
end

local refill = (elapsed_ms / 1000.0) * refill_rate
tokens = math.min(capacity, tokens + refill)

local allowed = 0
local retry_after_ms = 0

if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
else
    -- Compute how long until enough tokens will exist, so the caller can
    -- return a precise Retry-After header instead of guessing.
    local deficit = cost - tokens
    retry_after_ms = math.ceil((deficit / refill_rate) * 1000.0)
end

redis.call("HSET", bucket_key, "tokens", tostring(tokens), "last_refill_ms", tostring(now_ms))
redis.call("EXPIRE", bucket_key, ttl_seconds)

return {allowed, math.floor(tokens), retry_after_ms}
