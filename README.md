# Quizzly: An AI-Driven Tool for Automated Quiz Generation 

---

## 1. Problem Statement
In modern higher education, students are often overwhelmed by a high volume of static learning materials, such as lengthy PDF lecture notes and PowerPoint documents. Traditional study methods frequently involve passive reading, which is often cited as one of the least effective methods for long-term retention. To analyze the problem further in detail:
* **Passive Consumption:** Students struggle to identify key concepts within dense slides.
* **Lack of Feedback Loops:** Without immediate testing, students may suffer from the "illusion of competence", where they believe they understand the material until they are faced with an actual exam.

There is a significant need for a tool that bridges the gap between receiving information and mastering it through an immediate and customized active recall.

## 2. Project Objectives
The objective of this project is to develop a functional software tool that utilizes Large Language Models (LLMs) to transform static documents into interactive assessments. 
* **Automated Extraction:** To accurately parse and extract core academic concepts from multiple format documents.
* **Customizable Assessment:** To allow the user to define the quantity of the question extracted from the uploaded file.
* **Adaptive Learning:** To implement an "Error Notebook" feature that logs incorrect responses from the user, allowing for targeted re-study.
* **Prompt Optimization:** To evaluate which prompt engineering techniques (e.g., Chain-of-Thought, Few-Shot) produce the most valuable questions.

## 3. Key Features and Outcomes
* **Multi-Format Ingestion:** Support for PDF, PPT, and DOC file uploads.
* **Streamlit Web Application:** The tool will be developed as an interactive web-based application using the Streamlit framework to ensure a user-friendly and accessible interface.
* **Dynamic Quiz Engine:** A user interface to select the number of questions, with a hard quantity limit to ensure LLM focus and prevent "hallucinations" or context window degradation.
* **Pedagogical Alignment:** The quiz questions will be organized into different levels of difficulty based on a recognized educational framework called Bloom's Taxonomy. This ensures that the tool does not just test simple memory such as remembering a definition but also challenges students to use that information to solve practical problems.
* **The Error Notebook:** A persistent database (or local log) that stores failed questions and provides "hints" derived from the source text rather than just giving the answer.

## 4. Risks and Challenges
Developing an AI-driven educational tool involves locating several technical and ethical difficulties. To ensure the platform effectively bridges the gap between passive reading and active recall without introducing misinformation, the following challenges must be addressed:
* **Document Parsing Variability:** PDFs and PPTs often have inconsistent layouts, which may impact the accuracy of extracting core academic concepts.
* **Answer Hallucinations:** The model may generate plausible but incorrect content; therefore, all generated assessments must be strictly grounded in the uploaded text and validated for accuracy.
* **Context Limits:** Longer documents may exceed the LLM's context window, requiring sophisticated chunking and retrieval strategies to preserve the original meaning.

## 5. Prompt Engineering Approach
The core of the application lies in the sophistication of the prompts. This project will move beyond "zero-shot" prompting to ensure high-quality educational output.
* **Role Prompting & System Personas:** The system will be assigned the persona of a "Senior Instructional Designer and Subject Matter Expert". This forces the LLM to adopt a professional, academic tone and focus on conceptual clarity.
* **Few-Shot Ingestion:** To ensure the quiz questions are not too "shallow," the prompt will include examples of high-quality multiple-choice questions (MCQs) and their corresponding distractors (incorrect but plausible answers).
* **Chain-of-Thought (CoT) Prompting:** To generate the "Error Notebook" explanations, the app will use CoT. The prompt will instruct the model to:
    1. Identify the specific paragraph in the PDF where the answer resides.
    2. Explain the logic of why the correct answer is right and why the user's specific wrong choice was incorrect.
* **Output Structuring:** To ensure the app can parse the LLM's response, prompts will utilize JSON Schema enforcement. This ensures the "Quiz" is always returned in a format the python script can render into a UI.

## 6. Preliminary Research
The development of Quizzly is based on two main areas of research. These studies explain why the tool is supportive for students and how the technology will work.
* **Grounding AI in Specific Sources (RAG):** Studies indicate that Large Language Models (LLMs) perform more effectively when they are based on specific "source truths," like a PDF file, rather than depending on general information (Lewis et al., 2020). This project will employ an RAG-inspired prompting technique. This approach ensures that every response generated is directly connected to the content of the uploaded document.
* **The Power of Active Recall:** Studies in educational psychology suggest that the process of self-testing is more effective at strengthening neural pathways than simply re-reading information (Roediger & Karpicke, 2006). The "Error Notebook" feature is a practical application of this research, as it focuses on the concept of "desirable difficulty".

## 7. Conclusion
In conclusion, Quizzly represents a significant step forward in educational technology by leveraging Retrieval-Augmented Generation (RAG) to convert passive study materials into interactive learning experiences. By building evidence-based strategies like active recall and Bloom's Taxonomy, the tool effectively provides students with a robust framework for achieving long-term academic mastery.

---
## References
* Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., ... & Kiela, D. (2020). Retrieval-augmented generation for knowledge-intensive nlp tasks. Advances in Neural Information Processing Systems, 33, 9459-9474.
* Roediger III, H. L., & Karpicke, J. D. (2006). Test-enhanced learning: Taking memory tests improves long-term retention. Psychological Science, 17(3), 249-255.