--- API caller script
/superpowers:brainstorming I want to work on the task 3. First, I need to know what does SEC API return for different texts: are they text? pdf? .txt/.md files? Create a folder called task3/ and a script that fetch different documents according to different API calls with different arguments given.

the prompts should be put under task3/prompts. I need a toolbox first that takes all kinds of args, then a survey script that return few documents for survey. Keep the survey list to be small and compact: 5 filings should be enough for the start.

Put the response in the disk, and jsons in stdout. Also add the caching system that if we hit the same endpoint + key + ext, then we read from the disk first. You can create an index file for what we have in the disk. For survey list, Raise to 15 filings with 3 companies in each categories.

--- Key design
Now let's talk about how to proceed what wa want for task3. Can we look at three examples and discuss how shall we process in order to get the expected output? Let's start with the smallest ones that we have downloaded.

I want it to be more generalized: You mentioned short slice + key words, but these key words might never happen in 2012 documents. How can we determine which sections different slices of texts belong to without these keywords?

You are again defining key words, and the decision rule is also too specific. Please think of "how a human will proceed this if he has to read 1k documents". What will he asked himselves about different paragraphs, and how would he split different sections/pages to return a structured data?

There are too many edge cases and use LLM is much easier in this case. We need LLM fallback for TOC findings.

How about a huge refactor: we use LangGraph, with different nodes: "how to locate toc", "how to locate different pages", "Text for different Item", etc. The agent will then decide what to do
