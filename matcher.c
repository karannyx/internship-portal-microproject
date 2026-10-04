#include <stdio.h>
#include <string.h>
#include <stdlib.h>

// This macro makes the function exportable so Python can read it
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

// The Skill Matching Algorithm
EXPORT int get_match_score(const char* student_skills, const char* job_skills) {
    int match_count = 0;
    int total_job_skills = 0;
    
    // Create copies of the strings because tokenizing (strtok) modifies them
    char s_skills[512];
    char j_skills[512];
    strncpy(s_skills, student_skills, sizeof(s_skills));
    strncpy(j_skills, job_skills, sizeof(j_skills));

    // Break the job skills string into individual words separated by commas
    char *job_token = strtok(j_skills, ", ");
    while (job_token != NULL) {
        total_job_skills++;
        
        // Break student skills into words and compare
        char s_copy[512];
        strncpy(s_copy, s_skills, sizeof(s_copy));
        char *student_token = strtok(s_copy, ", ");
        
        while (student_token != NULL) {
            // If the skills match exactly
            if (strcmp(job_token, student_token) == 0) {
                match_count++;
                break;
            }
            student_token = strtok(NULL, ", ");
        }
        job_token = strtok(NULL, ", ");
    }

    if (total_job_skills == 0) return 0;
    
    // Return the percentage match
    return (match_count * 100) / total_job_skills; 
}