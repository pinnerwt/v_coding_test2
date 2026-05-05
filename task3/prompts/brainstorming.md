/superpowers:brainstorming I want to work on the task 3. First, I need to know what does SEC API return for different texts: are they text? pdf? .txt/.md files? Create a folder called task3/ and a script that fetch different documents according to different API calls with different arguments given.

the prompts should be put under task3/prompts. I need a toolbox first that takes all kinds of args, then a survey script that return few documents for survey. Keep the survey list to be small and compact: 5 filings should be enough for the start.

Put the response in the disk, and jsons in stdout. Also add the caching system that if we hit the same endpoint + key + ext, then we read from the disk first. You can create an index file for what we have in the disk. For survey list, Raise to 15 filings with 3 companies in each categories.
