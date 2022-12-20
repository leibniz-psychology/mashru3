;;; Copyright 2022 Leibniz Institute for Psychology
;;;
;;; Permission is hereby granted, free of charge, to any person obtaining a copy
;;; of this software and associated documentation files (the "Software"), to deal
;;; in the Software without restriction, including without limitation the rights
;;; to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
;;; copies of the Software, and to permit persons to whom the Software is
;;; furnished to do so, subject to the following conditions:
;;;
;;; The above copyright notice and this permission notice shall be included in
;;; all copies or substantial portions of the Software.
;;;
;;; THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
;;; IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
;;; FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
;;; AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
;;; LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
;;; OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
;;; SOFTWARE.

;; Helper to add/remove packages from manifests.

(use-modules (guix read-print) (ice-9 match) (srfi srfi-26) (srfi srfi-1) (gnu packages))

(define (filter-manifest manifest additions removals)
  "Filter a list of MANIFEST s-expressions and add specifications in
ADDITIONS, remove those in REMOVALS to SPECIFICATIONS->MANIFST. Leave
everything else untouched."
  (map (match-lambda
        (('specifications->manifest rest ...)
          (cons 'specifications->manifest
                (map (match-lambda
                      (('quote args)
                       (list 'quote (append (filter (lambda (x) (not (member x removals))) args)
                                            additions)))
                      (x x)) rest)))
        (x x))
       manifest))

(define (parse-args args)
  "Parse program arguments ARGS and separate into (ADDITIONS REMOVALS ...)."
  (define (parse s accum)
    (match accum
      ((additions removals ...)
        (let ((operation (string-ref s 0))
              (spec (substring s 1)))
          (match operation
            (#\+ `(,(cons spec additions) . ,removals))
            (#\- `(,additions . ,(cons spec removals)))
            (_    (raise-exception `(invalid-op ,operation ,s))))))))

  (fold parse '(() . ()) args))

(define (main)
  "Run main program."
  (let* ((args (parse-args (cdr (program-arguments))))
         (additions (car args))
         (removals (cdr args))
         (manifest (filter-manifest (read-with-comments/sequence (current-input-port)) additions removals)))
    (pretty-print-with-comments/splice (current-output-port) manifest)))

(with-exception-handler
  (lambda (e)
    (match e
      (('invalid-op op argument)
       (begin
         (format (current-error-port) "Invalid operation ~a in argument ~a.~%" op argument)
         (exit 2)))
      (_
       (begin
         (format #t "error occured: ~a~%" e)
         (exit 1)))))
  main
  #:unwind? #t)

