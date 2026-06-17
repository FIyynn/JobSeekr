# JobSeekr Fresh

Objective: tools for an LLM that allow it to reliably understand a webpage, decide what to do, and interact with it through a minimal, deterministic action interface.



* Leave the existing notebook alone 
* Add new test notebook to test features/new tools.
* use importlib reload on corresponding tool lib in each cell
* work on the html to markdown tool
* work on a function that that takes input string(llm response) and parses "<cmd>example tool call(x, y, z)<cmd>"
* work on a tool that allows llm to interact with the webpage
eg. browser(action = "click", element\_id = "485")
browser(action = "input\_text", element\_id = "485"input = "example@email.com")
and toggle check box, click radio box, select from drop down, etc... keep tools simple for llm
* make sure html to markdown prevents any duplicated lines under each other, unnecessary empty lines. keep response structured, make sure output is simple for llm
* validate html-markdown completeness on various webpages, specifically on lazy load websites/drop down menus, etc...
* also need file upload tool for the llm to upload documents eg. CV.pdf
* Back/forward/reload tool
* 

