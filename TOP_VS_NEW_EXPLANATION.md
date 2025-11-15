# Reddit scraper.top() vs scraper.new() - Key Differences

## What Changed

I've modified your script to use `subreddit.top()` instead of `subreddit.new()`. Here are the key differences:

### Original Script (`subreddit.new()`)
- Fetches posts sorted by **newest first**
- Posts are in chronological order (most recent → oldest)
- Good for: Getting all recent posts, monitoring new content

### Modified Script (`subreddit.top()`)
- Fetches posts sorted by **highest score (upvotes)**  
- Posts are ranked by popularity/engagement
- Good for: Getting the best/most popular posts

## Main Changes in the Code

### 1. Changed the Fetching Method

**Before:**
```python
submissions_in_batch = [
    s async for s in subreddit.new(limit=BATCH_SIZE, params=params)
]
```

**After:**
```python
submissions_in_batch = [
    s async for s in subreddit.top(time_filter=time_filter, limit=BATCH_SIZE, params=params)
]
```

### 2. Added Time Filter Parameter

The `top()` method requires a `time_filter` parameter that determines the time period for ranking:

- **`'all'`** - Top posts of all time (most popular)
- **`'year'`** - Top posts from the past year
- **`'month'`** - Top posts from the past month  
- **`'week'`** - Top posts from the past week
- **`'day'`** - Top posts from the past day

The script now asks you to choose this when running.

### 3. Date Filtering Still Works

Even though you're fetching by TOP score, the script still filters posts by your chosen date:

```python
submissions_to_process = [
    s for s in submissions_in_batch
    if s.id not in existing_ids and s.created_utc >= end_timestamp
]
```

This means you get the **top posts within your specified date range**.

## How to Use

### Run the script:
```bash
python reddit_scraper_top.py
```

### You'll be asked for:
1. **End date** (e.g., `2023-01-01`) - Only posts from this date forward will be fetched
2. **Time filter** - Choose from:
   - `1` - All time (recommended for comprehensive results)
   - `2` - Year
   - `3` - Month
   - `4` - Week  
   - `5` - Day

## Important Notes

### ⚠️ Still Limited to ~1000 Posts

The Reddit API **still has the same ~1000 post limit** whether you use `new()` or `top()`. This is a Reddit API restriction, not a PRAW or script limitation.

**Why this matters:**
- If your subreddit has more than 1000 top posts since your date, you'll only get the top 1000 most popular ones
- If you need ALL posts (not just the most popular), you should use the original `new()` version

### When to Use Each Version

**Use `subreddit.top()` (this new version) when:**
- ✅ You want the most popular/engaging posts
- ✅ You're doing analysis on highly-upvoted content
- ✅ You want posts with the most discussion/comments
- ✅ Quality over quantity

**Use `subreddit.new()` (original version) when:**
- ✅ You want ALL posts, not just popular ones
- ✅ You're doing comprehensive data collection
- ✅ You want chronological order
- ✅ You care about recent activity

### Example Scenarios

**Scenario 1: Studying the most popular exam prep strategies**
```
Use: reddit_scraper_top.py
Time filter: all
Date: 2023-01-01
Result: Top 1000 most upvoted posts since 2023 (the "best" content)
```

**Scenario 2: Monitoring all recent posts**
```
Use: Original script (with new())
Date: 7 days ago
Result: ALL posts from the last week, regardless of popularity
```

**Scenario 3: Most popular posts this month**
```
Use: reddit_scraper_top.py
Time filter: month
Date: (ignored, will get current month's top posts)
Result: Top posts from this month only
```

## Combined Approach (Best of Both Worlds)

If you want comprehensive data:
1. Run the `new()` version to get ALL posts chronologically
2. Then run the `top()` version to ensure you got all high-quality posts
3. The database will automatically deduplicate (same post won't be added twice)

## File Comparison

| Feature | Original Script | New Script (TOP) |
|---------|----------------|------------------|
| **Sort order** | Newest first | Highest score first |
| **Time filter** | Not applicable | all/year/month/week/day |
| **Best for** | Complete history | Popular content |
| **Post limit** | ~1000 | ~1000 |
| **Date filtering** | ✅ Yes | ✅ Yes |

## Troubleshooting

### "I'm still not getting all posts from my date"
- This is the 1000-post Reddit API limit
- If there are more than 1000 top posts since your date, you'll only get the top 1000
- Consider using `time_filter='all'` to get the absolute highest scoring posts

### "Posts aren't in chronological order"
- This is expected! `top()` sorts by score, not date
- If you need chronological order, use the original `new()` version

### "I want both popular AND all posts"
- Run both scripts!
- The database will deduplicate automatically
- This gives you comprehensive coverage
