import type { InterviewSession, Question, QuestionResponse } from "@/types/api";

export type { Question, QuestionResponse };

export interface AnswerOutResponse {
  evaluation: QuestionResponse;
  session: InterviewSession;
  replan_execution_id: string | null;
}
