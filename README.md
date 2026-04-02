# st-Quizzly
This is the streamlit app for quiz generation

# [cite_start]Quizzly: An AI-Driven Tool for Automated Quiz Generation [cite: 2]

---

## 1. Problem Statement
[cite_start]In modern higher education, students are often overwhelmed by a high volume of static learning materials, such as lengthy PDF lecture notes and PowerPoint documents[cite: 6]. [cite_start]Traditional study methods frequently involve passive reading, which is often cited as one of the least effective methods for long-term retention[cite: 7]. [cite_start]To analyze the problem further in detail[cite: 8]:
* [cite_start]**Passive Consumption:** Students struggle to identify key concepts within dense slides[cite: 9].
* [cite_start]**Lack of Feedback Loops:** Without immediate testing, students may suffer from the "illusion of competence", where they believe they understand the material until they are faced with an actual exam[cite: 10].

[cite_start]There is a significant need for a tool that bridges the gap between receiving information and mastering it through an immediate and customized active recall[cite: 11].

## 2. Project Objectives
[cite_start]The objective of this project is to develop a functional software tool that utilizes Large Language Models (LLMs) to transform static documents into interactive assessments[cite: 13]. 
* [cite_start]**Automated Extraction:** To accurately parse and extract core academic concepts from multiple format documents[cite: 14].
* [cite_start]**Customizable Assessment:** To allow the user to define the quantity of the question extracted from the uploaded file[cite: 15].
* [cite_start]**Adaptive Learning:** To implement an "Error Notebook" feature that logs incorrect responses from the user, allowing for targeted re-study[cite: 16].
* [cite_start]**Prompt Optimization:** To evaluate which prompt engineering techniques (e.g., Chain-of-Thought, Few-Shot) produce the most valuable questions[cite: 17, 18].

## 3. Key Features and Outcomes
* [cite_start]**Multi-Format Ingestion:** Support for PDF, PPT, and DOC file uploads[cite: 20].
* [cite_start]**Streamlit Web Application:** The tool will be developed as an interactive web-based application using the Streamlit framework to ensure a user-friendly and accessible interface[cite: 21].
* [cite_start]**Dynamic Quiz Engine:** A user interface to select the number of questions, with a hard quantity limit to ensure LLM focus and prevent "hallucinations" or context window degradation[cite: 22].
* [cite_start]**Pedagogical Alignment:** The quiz questions will be organized into different levels of difficulty based on a recognized educational framework called Bloom's Taxonomy[cite: 23]. [cite_start]This ensures that the tool does not just test simple memory such as remembering a definition but also challenges students to use that information to solve practical problems[cite: 24].
* [cite_start]**The Error Notebook:** A persistent database (or local log) that stores failed questions and provides "hints" derived from the source text rather than just giving the answer[cite: 25].

## 4. Risks and Challenges
[cite_start]Developing an AI-driven educational tool involves locating several technical and ethical difficulties[cite: 27]. [cite_start]To ensure the platform effectively bridges the gap between passive reading and active recall without introducing misinformation, the following challenges must be addressed[cite: 28]:
* [cite_start]**Document Parsing Variability:** PDFs and PPTs often have inconsistent layouts, which may impact the accuracy of extracting core academic concepts[cite: 28].
* [cite_start]**Answer Hallucinations:** The model may generate plausible but incorrect content; therefore, all generated assessments must be strictly grounded in the uploaded text and validated for accuracy[cite: 29, 30].
* [cite_start]**Context Limits:** Longer documents may exceed the LLM's context window, requiring sophisticated chunking and retrieval strategies to preserve the original meaning[cite: 31].

## 5. Prompt Engineering Approach
[cite_start]The core of the application lies in the sophistication of the prompts[cite: 33]. [cite_start]This project will move beyond "zero-shot" prompting to ensure high-quality educational output[cite: 34].
* [cite_start]**Role Prompting & System Personas:** The system will be assigned the persona of a "Senior Instructional Designer and Subject Matter Expert"[cite: 35]. [cite_start]This forces the LLM to adopt a professional, academic tone and focus on conceptual clarity[cite: 36].
* [cite_start]**Few-Shot Ingestion:** To ensure the quiz questions are not too "shallow," the prompt will include examples of high-quality multiple-choice questions (MCQs) and their corresponding distractors (incorrect but plausible answers)[cite: 37, 38].
* [cite_start]**Chain-of-Thought (CoT) Prompting:** To generate the "Error Notebook" explanations, the app will use CoT[cite: 39]. [cite_start]The prompt will instruct the model to[cite: 40]:
    1. [cite_start]Identify the specific paragraph in the PDF where the answer resides[cite: 41].
    2. [cite_start]Explain the logic of why the correct answer is right and why the user's specific wrong choice was incorrect[cite: 42, 43].
* [cite_start]**Output Structuring:** To ensure the app can parse the LLM's response, prompts will utilize JSON Schema enforcement[cite: 44, 45]. [cite_start]This ensures the "Quiz" is always returned in a format the python script can render into a UI[cite: 46].

## 6. Preliminary Research
[cite_start]The development of Quizzly is based on two main areas of research[cite: 48]. [cite_start]These studies explain why the tool is supportive for students and how the technology will work[cite: 49].
* [cite_start]**Grounding AI in Specific Sources (RAG):** Studies indicate that Large Language Models (LLMs) perform more effectively when they are based on specific "source truths," like a PDF file, rather than depending on general information (Lewis et al., 2020)[cite: 50, 51]. [cite_start]This project will employ an RAG-inspired prompting technique[cite: 52]. [cite_start]This approach ensures that every response generated is directly connected to the content of the uploaded document[cite: 52].
* [cite_start]**The Power of Active Recall:** Studies in educational psychology suggest that the process of self-testing is more effective at strengthening neural pathways than simply re-reading information (Roediger & Karpicke, 2006)[cite: 53, 54]. [cite_start]The "Error Notebook" feature is a practical application of this research, as it focuses on the concept of "desirable difficulty"[cite: 55].

## 7. Conclusion
[cite_start]In conclusion, Quizzly represents a significant step forward in educational technology by leveraging Retrieval-Augmented Generation (RAG) to convert passive study materials into interactive learning experiences[cite: 57, 58]. [cite_start]By building evidence-based strategies like active recall and Bloom's Taxonomy, the tool effectively provides students with a robust framework for achieving long-term academic mastery[cite: 59].

---
## References
* [cite_start]Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., ... & Kiela, D. (2020)[cite: 61]. [cite_start]Retrieval-augmented generation for knowledge-intensive nlp tasks[cite: 62]. [cite_start]Advances in Neural Information Processing Systems, 33, 9459-9474[cite: 62].
* [cite_start]Roediger III, H. L., & Karpicke, J. D. (2006)[cite: 63]. [cite_start]Test-enhanced learning: Taking memory tests improves long-term retention[cite: 63]. [cite_start]Psychological Science, 17(3), 249-255[cite: 64].