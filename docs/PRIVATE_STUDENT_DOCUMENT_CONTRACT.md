# Private student documents

Implementation status: pending. ExamArena currently stores shared teaching PDFs
in `CourseMaterial`; its private student profiles and exam histories use separate
authorization checks. A private student PDF needs an explicit owner and subject
contract before that upload/search feature is enabled.

| Resource | Authority | Readers |
| --- | --- | --- |
| Standalone document | Authenticated owner user ID | Its owner |
| Medical report | Quick-Clinic patient authorization and delegated patient subject | Authorized patient flows and doctors authorized for that patient |
| Shared teaching material | ExamArena school and optional class | Enrolled students and authorized staff in that sharing scope |
| Private student report | Stable student owner user ID, document school, and current approved enrollment/staff grants | Owner, teachers assigned to that student's current class, and principal of the authorized school |

Other students do not gain private-report access by sharing a class. A teacher's
school affiliation alone does not grant access to every student. A principal's
authority remains limited to their school. Original uploader and student owner
are distinct: a report uploaded by a teacher still belongs to its student owner.

## Existing authority and missing storage

ExamArena's `User`, `Student`, `School`, `SchoolClass`, `Teacher`, `TeacherClass`
and `Principal` records already express identity, enrollment and staff authority.
The merged [access resolver](https://github.com/karanagg166/ExamArena/blob/main/backend/app/core/school_access.py)
reads those current PostgreSQL grants. It recognizes both explicit teaching
assignments and the existing primary teacher; a principal must own the school or
have a valid principal affiliation. An empty class grant never means all classes.
The [student routes](https://github.com/karanagg166/ExamArena/blob/main/backend/app/students/router.py)
apply that resolver to private profiles and exam histories. These existing
structured records are protected; they are not a private PDF catalog.

Search Sphere already stores delegated `client_id`, `tenant_id`, `collection_id`
and `owner_subject_id` on external documents, plus scoped conversations,
published versions and durable deletion receipts. Reuse those concepts. The
missing behavior is an ExamArena student-owned PDF catalog and an enforceable
mandatory-subject policy across Search Sphere's private-collection operations.
An optional subject filter alone does not establish that policy. Shared
`CourseMaterial` rows must not be repurposed as private student files.

The implementation must first map existing user, student, school, class and
teaching-assignment tables. Store ownership by stable user identity so deleting a
class or recreating an enrollment cannot change a private file into shared
material or silently delete the owner's file. Introduce only the missing document
ownership/provenance records and tracked migrations. Keep these backend tables
unavailable through anonymous or unintended authenticated Data API access.

## Delegation through the versioned API

ExamArena authenticates the caller and resolves the target student from PostgreSQL
on every operation. It verifies self access or the current school/class staff
grant, then delegates the registered client, document tenant, private collection,
and mandatory student subject to Search Sphere using a server-only credential.
The browser cannot choose an identity, tenant or role as authority.

Use a subject independent of the student's class. Moving within a school updates
the authorization grant without changing the student's document identity.
Search Sphere must require the delegated subject for every private-collection
operation, including registration, metadata, download, polling, search, answers,
citations, history, deletion and cleanup. Its private-collection policy must reject
an omitted subject; a broadly registered backend key is not sufficient authority
for an unscoped request.

School transfer must preserve ownership and original provenance. Do not copy or
share a document into a new school's tenant automatically. Teacher/principal
access requires matching document-school authority and current enrollment. A
cross-school document move or new-school grant needs a separate explicit transfer
operation and authorization policy. The owner retains access to their own files.

Reauthorize after upstream work and before returning any text or citation.
Revoking an assignment must withhold the answer, including cached answers. Memory
stores source identities; source permissions and current versions are checked
again before reuse. Private history and caches include the actor, student subject,
classification and document versions. Cleanup verification binds both collection
and student subject against the durable deletion receipt after local deletion.

## Processing and acceptance

Private access is separate from permission to send data to an inference provider.
Default unknown student reports to confidential. Keep originals in private
Storage and fail closed for hosted inference until an eligible provider and
scope-specific approval exist. Only approved public/synthetic fixtures may use
the configured Cohere trial during verification.

Required tests include two students in the same class, an unassigned teacher in
the same school, another school, authorized teachers/principals, revocation during
generation, a class deletion, an enrollment change, an omitted/wrong delegated
subject, and physically verified deletion. The live release must upload a new
owned synthetic PDF, retrieve its unique marker, validate its grounded citations,
and prove both student isolation and cleanup before this feature is marked done.
