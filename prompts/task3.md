--- API caller script
/superpowers:brainstorming I want to work on the task 3. First, I need to know what does SEC API return for different texts: are they text? pdf? .txt/.md files? Create a folder called task3/ and a script that fetch different documents according to different API calls with different arguments given.

the prompts should be put under task3/prompts. I need a toolbox first that takes all kinds of args, then a survey script that return few documents for survey. Keep the survey list to be small and compact: 5 filings should be enough for the start.

Put the response in the disk, and jsons in stdout. Also add the caching system that if we hit the same endpoint + key + ext, then we read from the disk first. You can create an index file for what we have in the disk. For survey list, Raise to 15 filings with 3 companies in each categories.

--- Claude Skill before we have an agent.
We are working on task3/ . I would like to create a skill that read SEC 10K documents. Let's call it /10k_extraction . Can you run the fetcher script and try to return the output format in @AI-Coding-Test-EN.md task3 ? We will iterate the skill on different documents to refine it later.
Let's stop here first. Can you update the skill to - What elements did you try to spot first when encountering a new document. Focus on "what functions did you call" and "why you end up designing the function like that" . The idea is that when we run the skill on a new document, it can reproduce a new script with less token usage and with a more structural approach.
Note that if an item is cut to different parts in the document, we should output multiple, since the start_range and end_range is fixed per output
Let's work on a refactor of what python codes you have used first. You tried to find occurrence of different words, different regex expressions, first 3 KBs and others. Can you make them a tool script with different arguments so that you can reuse them easily next time?
