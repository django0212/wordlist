# Reddit API 1000-Post Limitation Explained

## The Problem

You're getting **999 posts instead of all posts from 2023-01-01** because of a **hard Reddit API limitation**.

### Key Facts:

1. **Reddit's listing endpoints** (like `/r/subreddit/new`) have a **HARD LIMIT of ~1000 posts**
2. This is **NOT a PRAW limitation** - it's Reddit's API design
3. You can **NEVER** fetch more than ~1000 posts using standard pagination with `subreddit.new()`
4. The 999 count is likely because:
   - One post was deleted/removed
   - One post was already in your database
   - Reddit's API sometimes returns slightly less than 1000

## Why This Happens

Reddit's API pagination works like this:

```
Request 1: Get posts 1-100 (using no "after" parameter)
Request 2: Get posts 101-200 (using "after" = fullname of post 100)
Request 3: Get posts 201-300 (using "after" = fullname of post 200)
...
Request 10: Get posts 901-1000 (using "after" = fullname of post 900)
Request 11: Returns EMPTY or duplicate posts (API limit reached)
```

**After ~1000 posts, Reddit stops returning new posts**, regardless of:
- Your batch size
- How you paginate
- Which PRAW version you use
- Your API credentials

## Solutions

### Solution 1: Simple Fix (Acknowledge the Limit)

Use `reddit_scraper_simple_fix.py` - This version:
- ✅ Clearly tracks total posts seen
- ✅ Warns when hitting the 1000-post limit
- ✅ Works reliably within Reddit's constraints
- ❌ Still limited to ~1000 posts per run

**Best for:** Smaller subreddits or periodic scraping

### Solution 2: Time-Based Chunking (Advanced)

Use `reddit_scraper_fixed.py` - This version:
- ✅ Uses Reddit's search API with time filters
- ✅ Can potentially fetch more than 1000 posts
- ✅ Chunks requests by time periods
- ⚠️ More complex and may hit rate limits
- ⚠️ Search API has its own quirks and limitations

**Best for:** Large subreddits with many historical posts

### Solution 3: Periodic Scraping (Recommended)

**Run your script regularly** to stay under the limit:
- Run daily/weekly to fetch new posts
- Each run processes only new posts since last run
- Never hit the 1000-post limit
- Most reliable approach

### Solution 4: External Tools

For complete historical data:
- **Pushshift.io** (if available - has been having issues)
- **Archive.org** Reddit archives
- **Third-party Reddit archives**

## What to Do Now

1. **Check your subreddit size:**
   ```python
   # How many posts does r/CATpreparation have since 2023-01-01?
   ```

2. **If < 1000 posts total:**
   - Something else is wrong
   - Check for deleted posts, private posts, or date filtering issues
   - Use `reddit_scraper_simple_fix.py` to see detailed counts

3. **If > 1000 posts total:**
   - You've hit Reddit's API limit (this is normal)
   - Use Solution 2 (time-based chunking) to get more
   - OR use Solution 3 (periodic scraping) for ongoing collection

## Testing the Fix

Run the simple fix version and check the output:

```bash
python reddit_scraper_simple_fix.py
```

Look for these lines:
```
-> Total posts seen so far: 999
WARNING: Reached Reddit's ~1000 post listing limit
```

This confirms you've hit Reddit's limit, not a code bug.

## Additional Notes

- The 1000-post limit is **per listing endpoint call chain**
- Some posts might be filtered out (deleted, removed by mods, etc.)
- Reddit's API might return slightly fewer than 1000 posts
- This limitation exists to prevent API abuse and reduce server load
