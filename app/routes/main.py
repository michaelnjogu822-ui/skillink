from flask import Blueprint, render_template
from sqlalchemy.orm import joinedload

from app.models import FreelancerProfile, Project, Review, User

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    featured = (User.query.join(FreelancerProfile).options(joinedload(User.freelancer_profile))
                .filter(User.is_active.is_(True), FreelancerProfile.headline.isnot(None))
                .order_by(FreelancerProfile.average_rating.desc(), FreelancerProfile.review_count.desc())
                .limit(3).all())
    testimonials = Review.query.options(
        joinedload(Review.reviewer), joinedload(Review.project).joinedload(Project.job)
    ).filter(Review.comment.isnot(None), Review.comment != "")\
        .order_by(Review.created_at.desc()).limit(3).all()
    return render_template("index.html", featured=featured, testimonials=testimonials)
