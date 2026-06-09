# agents/context_agent.py

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from typing import Dict, Any, Tuple
import logging
import datetime
from google.cloud import discoveryengine
import re, pytz
from google.protobuf.json_format import MessageToDict

logging.basicConfig(level=logging.INFO)

class PolicyContextAgent(LlmAgent):
    """
    A multi-tool agent responsible for RAG retrieval and compliance enforcement.
    """
    def __init__(self, project_id: str, location: str, engine_id: str, data_store_id: str, **kwargs):

        # 1. Define the RAG Tool (Simulated)
        rag_tool = FunctionTool(func=self._retrieve_policy_text)

        # 2. Define the Compliance Check Tool (Guardrail Logic)
        check_tool = FunctionTool(func=self._check_temporal_compliance)

        super().__init__(
            name="PolicyContextAgent",
            description="Manages policy grounding via RAG and executes compliance guardrails.",
            tools=[rag_tool, check_tool], # Registering both tools
            **kwargs
        )

        # Assign attributes *after* super().__init__ using __dict__ 
        # to prevent Pydantic initialization conflicts.
        self.__dict__['_project_id'] = project_id
        self.__dict__['_location'] = location
        self.__dict__['_engine_id'] = engine_id
        self.__dict__['_data_store_id'] = data_store_id

    # Tool 1: Retrieves Policy Text 
    def _retrieve_policy_text(self, doc_type: str, requested_role: str) -> Dict[str, Any]:
        """
        Retrieves relevant policy text and links from the Vertex AI Search (RAG)
        engine based on the access request context.
        
        Args:
            doc_type: The policy document ID/name (e.g., "baseline_policy_doc_123")
            requested_role: The IAM role being requested (used for context)
        """
        
        logging.info(f"RAG: Executing real query for doc_type='{doc_type}', role='{requested_role}'")

        # --- PRODUCTION ACTIVATION: Simulated RAG Result based on Policy ID ---
        # In a real environment, this would be a client call to Vertex AI Search or an internal
        # knowledge store, returning a structured summary of the policy.

        # The query should combine all context for the best grounding
        search_query = f"What is the policy based on document {doc_type}?"
        
        try:
            client = discoveryengine.SearchServiceClient()
            
            # Form the full name of the serving config
            # We assume a default serving configuration:
            serving_config_name = client.serving_config_path(
                project=self.__dict__['_project_id'],
                location=self.__dict__['_location'],
                data_store=self.__dict__['_data_store_id'], # Use data_store_id here
                serving_config='default_config' # Standard ID for a basic configuration
            )

            # Add content search spec for better extraction
            content_search_spec = discoveryengine.SearchRequest.ContentSearchSpec(
                # Request extractive segments (more verbose content)
                extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_segment_count=3,  # Up to 3 segments per result
                    max_extractive_answer_count=3,   # Up to 3 answers per result
                    return_extractive_segment_score=False
                ),
                # Also request snippets as fallback
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True
                )
            )

            # Ask Vertex AI Search to generate a concise server-side summary
            summary_spec = discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(
                summary_result_count=1,
                include_citations=False,
                ignore_adversarial_query=True,
                ignore_non_summary_seeking_query=False,
                # Add a prompt to guide the model's summary length
                model_prompt_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec.ModelPromptSpec(
                    preamble="Generate a very concise summary, strictly under 100 characters, explaining the core policy."
                )
            )

            # Filter to the exact policy doc via its metadata field. The JSONL
            # import sets structData.policy_id, which surfaces in the data store
            # schema as the indexable field "policy_id" -- filter expressions
            # reference that schema field name, not the structData. import path.
            filter_str = f'policy_id: ANY("{doc_type}")'

            response = client.search(
                request=discoveryengine.SearchRequest(
                    serving_config=serving_config_name,
                    query=search_query,
                    filter=filter_str,
                    page_size=3, # In many library versions, summary_spec is part of content_search_spec
                    content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                        snippet_spec=content_search_spec.snippet_spec,
                        summary_spec=summary_spec,
                        extractive_content_spec=content_search_spec.extractive_content_spec
                    ),
                )
            )

            logging.info(f"RAG Response type: {type(response)}")
            logging.info(f"Number of results: {len(response.results)}")

            if response.results:
                first_result = response.results[0]
                logging.info(f"First result type: {type(first_result)}")
                logging.info(f"First result dir: {dir(first_result)}")
                
                if first_result.document:
                    logging.info(f"Document fields: {first_result.document}")
                    if first_result.document.derived_struct_data:
                        logging.info(f"Struct data keys: {list(first_result.document.derived_struct_data.keys())}")

            policy_text = []
            source_links = []

            # Process results - extractive_segments and extractive_answers are in derived_struct_data
            for result in response.results:
                if not result.document:
                    continue
                    
                doc = result.document
                
                # Extract from derived_struct_data (this is where extractive content lives)
                if hasattr(doc, 'derived_struct_data') and doc.derived_struct_data:
                    struct_data = doc.derived_struct_data

                    logging.info(f"Processing struct_data type: {type(struct_data)}")

                    # MapComposite objects can be accessed like dicts
                    # Method 1: Extract extractive_segments (recommended for unstructured data)
                    if 'extractive_segments' in struct_data:
                        segments = struct_data['extractive_segments']
                        logging.info(f"extractive_segments type: {type(segments)}")

                        # segments is a ListValue-like object, iterate directly
                        for segment in segments:
                            logging.info(f"segment type: {type(segment)}")
                            # segment is a dict-like structure
                            if 'content' in segment:
                                content = segment['content']
                                if content:
                                    policy_text.append(content)
                                    logging.info(f"✓ Extracted segment content (first 100 chars): {content[:100]}")
                        
                    # Method 2: Extract extractive_answers (shorter, more precise)
                    if 'extractive_answers' in struct_data:
                        answers = struct_data['extractive_answers']
                        logging.info(f"extractive_answers type: {type(answers)}")
                        
                        for answer in answers:
                            logging.info(f"answer type: {type(answer)}")
                            if 'content' in answer:
                                content = answer['content']
                                if content:
                                    policy_text.append(content)
                                    logging.info(f"✓ Extracted answer content (first 100 chars): {content[:100]}")
                        
                    # Fallback: Try snippets if no extractive content found
                    # Fallback: Try snippets if no extractive content found
                    if not policy_text and 'snippets' in struct_data:
                        snippets = struct_data['snippets']
                        logging.info(f"snippets type: {type(snippets)}")
                        
                        for snippet in snippets:
                            logging.info(f"snippet type: {type(snippet)}")
                            if 'snippet' in snippet:
                                content = snippet['snippet']
                                if content:
                                    # Remove HTML tags from snippet
                                    import re
                                    content = re.sub(r'<[^>]+>', '', content)
                                    content = content.replace('&nbsp;', ' ')
                                    policy_text.append(content)
                                    logging.info(f"✓ Extracted snippet (first 100 chars): {content[:100]}")
                        
                    # Extract source link
                    if 'link' in struct_data:
                        link = struct_data['link']
                        if link:
                            source_links.append(link)
                            logging.info(f"✓ Extracted link: {link}")

                elif isinstance(struct_data, dict):
                    logging.info(f"DEBUG: struct_data is a dict")
                    logging.info(f"DEBUG: dict keys: {list(struct_data.keys())}")

                    # Try extracting from dict
                    if 'extractive_segments' in struct_data:
                        for segment in struct_data['extractive_segments']:
                            if isinstance(segment, dict) and 'content' in segment:
                                policy_text.append(segment['content'])
                    
                    if 'link' in struct_data:
                        source_links.append(struct_data['link'])
                else:
                    logging.warning(f"DEBUG: Unknown struct_data type: {type(struct_data)}")

            # Combine the retrieved snippets into a single context string for the LLM
            rag_context = "\n---\n".join(policy_text)

            if not rag_context:
                logging.warning(f"No extractive content found for query: {search_query}")
                logging.warning("This usually means Enterprise features are not enabled or documents are not indexed properly")
                # Log sample structure for debugging
                if response.results and len(response.results) > 0:
                    sample = response.results[0]
                    logging.info(f"Sample result structure - has document: {hasattr(sample, 'document')}")
                    if hasattr(sample, 'document') and sample.document:
                        logging.info(f"Document has derived_struct_data: {hasattr(sample.document, 'derived_struct_data')}")

            # Detect constraint type from the retrieved policy text
            constraint_type = self._detect_constraint_type(rag_context)

            # Extract justification summary from policy text (first 200 chars)
            #justification_summary = rag_context[:200] + "..." if len(rag_context) > 200 else rag_context
            justification_summary = self._build_justification_summary(response, rag_context)

            return {
                "rag_context": rag_context,
                "source_links": list(set(source_links)),
                "query": search_query,
                "retrieval_success": bool(policy_text),
                "constraint_type": constraint_type,
                "justification_summary": justification_summary,
                "baseline_policy_doc_id": doc_type
            }
        except Exception as e:
            logging.error(f"RAG Query FAILED: {e}")
            return {
                "rag_context": "RAG retrieval failed. Defaulting to high-risk constraint.",
                "source_links": [],
                "query": search_query,
                "retrieval_success": False,
                "constraint_type": "guardrail_deny",
                "justification_summary": "Policy retrieval failed - access denied by default",
                "baseline_policy_doc_id": doc_type
        }

    def _build_justification_summary(self, response, fallback_text: str, max_chars: int = 200) -> str:
        """
        Attempts to use the Vertex AI search response summary. Falls back to retrieved policy text when summarization is unavailable. 
        """
        summary_text = ""
        summary = getattr(response, "summary", None)

        if summary:
            summary_text = getattr(summary, "summary_text", "") or getattr(summary, "summary", "")
            if not summary_text and hasattr(summary, "summary_texts"):
                summary_texts = getattr(summary, "summary_texts")
                if summary_texts:
                    first_summary = summary_texts[0]
                    summary_text = getattr(first_summary, "text", "") or first_summary.get("text", "") if isinstance(first_summary, dict) else ""
        
            if not summary_text:
                try:
                    summary_dict = MessageToDict(summary)
                    summary_texts = summary_dict.get("summaryTexts", []) or summary_dict.get("summary_texts", [])
                    if summary_texts:
                        summary_text = summary_texts[0].get("text", "")
                    elif summary_dict.get("summaryText"):
                        summary_text = summary_dict["summaryText"]
                except Exception as e:
                    logging.debug(f"Unable to parse summary text from response: {e}")

        final_summary = summary_text.strip() if summary_text else fallback_text.strip()

        if len(final_summary) > max_chars:
            return final_summary[:max_chars].rstrip() + "..."
        return final_summary        
        
    def _detect_constraint_type(self, policy_text: str) -> str:
        """
        Analyzes retrieved policy text to determine the constraint type.
        Returns: 'temporal_access_only', 'guardrail_deny', or 'none'
        """
        policy_lower = policy_text.lower()
        
        logging.info(f"Analyzing policy text (length: {len(policy_text)} chars)")
    
        # Check for explicit denial keywords first
        deny_keywords = ["access denied", "forbidden", "prohibited", "not allowed", "not permitted"]
        if any(keyword in policy_lower for keyword in deny_keywords):
            logging.info("Constraint detected: guardrail_deny")
            return "guardrail_deny"
        
        # Use regex for temporal patterns - more flexible
        temporal_patterns = [
            r"temporal access.*only",  # Matches "temporal access [anything] only"
            r"\d{2}:\d{2}\s*(and|to|-)\s*\d{2}:\d{2}",  # Matches time ranges like "08:00 and 17:00"
            r"business hours.*only",
            r"time-restricted",
            r"monday\s+to\s+friday",
            r"between\s+\d{1,2}:\d{2}"  # Matches "between 08:00"
        ]
        
        for pattern in temporal_patterns:
            if re.search(pattern, policy_lower):
                logging.info(f"✓ Constraint detected: temporal_access_only (matched pattern: '{pattern}')")
                return "temporal_access_only"
        
        logging.info("Constraint detected: none (standard access)")
        return "none"

    # Tool 2: Executes Compliance Check (Guardrail - **ACTIVATED**)
    def _check_temporal_compliance(self, constraint_type: str, user_timezone: str = 'UTC') -> Tuple[bool, str]:
        """
        Executes a context-aware security check (e.g., temporal restriction) against the 
        current time to enforce policy guardrails.
        """
        logging.info(f"Compliance Check: Executing guardrail for constraint_type: {constraint_type}")
        
        # 1. TEMPORAL CHECK
        if constraint_type == "temporal_access_only":
            
            try: 
                # Get user's timezone
                user_tz = pytz.timezone(user_timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                logging.error(f"Invalid timezone '{user_timezone}', falling back to UTC")
                user_tz = pytz.UTC

            now = datetime.datetime.now(user_tz)
            current_hour = now.hour
            current_weekday = now.weekday()  # Monday=0, Sunday=6

            logging.info(f"Current time in {user_tz.zone}: {now.strftime('%Y-%m-%d %H:%M:%S %A %Z')}")
            logging.info(f"Current hour: {current_hour}, Current weekday: {current_weekday} (0=Mon, 6=Sun)")
            
            # Constraint is 08:00 to 17:00 (8 AM to 5 PM, non-inclusive of 17:00).
            # First check: Must be Monday-Friday (weekday 0-4)
            if current_weekday > 4:  # Saturday=5, Sunday=6
                day_name = now.strftime('%A')
                logging.warning(f"Compliance Check: FAIL - Today is {day_name}, access only permitted Monday-Friday")
                return False, f"Temporal access restricted to business days (Monday-Friday). Today is {day_name}."
            
            # Second check: Must be between 08:00 and 17:00 (8 AM to 5 PM, non-inclusive of 17:00)
            if current_hour >= 8 and current_hour < 17:
                logging.info(f"Compliance Check: PASS - {now.strftime('%A')} at hour {current_hour} is within business hours (Mon-Fri, 08:00-17:00)")
                return True, "Access granted: within business hours (Monday-Friday, 08:00-17:00)."
            else:
                logging.warning(f"Compliance Check: FAIL - Hour {current_hour} is outside business hours (08:00-17:00)")
                return False, f"Temporal access restricted to business hours (08:00-17:00). Current time: {now.strftime('%H:%M')}."
        
        # 2. IMMEDIATE DENY CHECK
        elif constraint_type == "guardrail_deny":
            logging.warning("Compliance Check: FAIL - Policy explicitly forbidden by automated guardrail.")
            return False, "Access explicitly forbidden by policy context guardrail."
            
        # 3. NO CONSTRAINT - Standard Access
        elif constraint_type == "none":
            logging.info("Compliance Check: PASS - No temporal or access restrictions")
            return True, "Standard access policy with no time restrictions."
        
        # 4. DEFAULT PASS
        return True, "No temporal or explicit compliance constraints applied."
